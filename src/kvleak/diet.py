"""What filled the context, and what each piece cost to carry.

Compaction and TTLs treat the context as a blob. This module opens it up: every
tool result, user message and assistant turn in a Claude Code transcript is a
block that entered the context at some call and was re-read on every call after
it, until a compaction dropped it. The biggest blocks are usually a handful of
tool results (a whole file, a long test log), and they are the cheapest thing to
fix: read with a range, pipe through `tail`, or let a subagent read and report.

Token counts are calibrated against the API's own usage numbers: the tokens a
call added to the prompt (from `usage`) are split across the blocks that arrived
since the previous call, in proportion to their estimated size. Images are
priced by their pixel count, like the API does, not by their base64 length.
"""

from __future__ import annotations

import base64
import json
import os
import struct
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

from .pricing import Price, price_for
from .sources import Transcript

CHARS_PER_TOKEN = 4.0
IMAGE_MAX_TOKENS = 1_600  # the API downsizes images to ~1.15 MP; tokens ≈ pixels / 750
USER_KIND = "your messages"
ASSISTANT_KIND = "assistant output"
SYSTEM_KIND = "system prompt & tools"
PLAIN_KINDS = (USER_KIND, ASSISTANT_KIND, SYSTEM_KIND)  # not tool results


@dataclass
class Block:
    kind: str  # tool name, USER_KIND or ASSISTANT_KIND
    label: str  # what it was: file path, command, first words
    call: int  # index of the call that first sent it
    est: float  # estimated tokens before calibration
    tokens: float = 0.0  # after calibration
    carried: int = 0  # calls that re-read it
    carry_cost: float = 0.0  # base-equivalent tokens
    carry_usd: float = 0.0
    is_image: bool = False


@dataclass
class Diet:
    session_id: str
    calls: int
    peak: int  # largest prompt seen
    blocks: List[Block] = field(default_factory=list)
    compactions: int = 0

    def by_kind(self) -> Dict[str, dict]:
        out: Dict[str, dict] = {}
        for b in self.blocks:
            d = out.setdefault(b.kind, {"count": 0, "tokens": 0.0, "carry_usd": 0.0, "images": 0, "max": 0.0})
            d["count"] += 1
            d["tokens"] += b.tokens
            d["carry_usd"] += b.carry_usd
            d["images"] += int(b.is_image)
            d["max"] = max(d["max"], b.tokens)
        return dict(sorted(out.items(), key=lambda kv: -kv[1]["carry_usd"]))

    @property
    def total_tokens(self) -> float:
        return sum(b.tokens for b in self.blocks)

    @property
    def carry_usd(self) -> float:
        return sum(b.carry_usd for b in self.blocks)


def _image_tokens(block: dict) -> float:
    src = block.get("source") or {}
    data = src.get("data") or ""
    try:
        head = base64.b64decode(data[:64] + "==")
        if head[:8] == b"\x89PNG\r\n\x1a\n":
            w, h = struct.unpack(">II", head[16:24])
            return min(w * h / 750.0, IMAGE_MAX_TOKENS)
    except Exception:
        pass
    return IMAGE_MAX_TOKENS


def _content_est(content) -> Tuple[float, bool]:
    """(estimated tokens, contains an image) for a tool_result / message content."""
    if isinstance(content, str):
        return len(content) / CHARS_PER_TOKEN, False
    est, image = 0.0, False
    for part in content or []:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "image":
            est += _image_tokens(part)
            image = True
        elif part.get("type") == "text":
            est += len(part.get("text") or "") / CHARS_PER_TOKEN
        else:
            est += len(json.dumps(part)) / CHARS_PER_TOKEN
    return est, image


def _label(name: str, inp: dict) -> str:
    inp = inp or {}
    for key in ("file_path", "command", "pattern", "description", "query", "url", "notebook_path", "skill", "prompt"):
        v = inp.get(key)
        if isinstance(v, str) and v:
            v = v.strip().splitlines()[0]
            if key == "file_path":
                v = os.path.basename(v) or v
            return v[:48]
    return name


def read_diet(path: str, price: Optional[Price] = None, miss_rate: float = 0.02) -> Diet:
    pending: List[Block] = []
    blocks: List[Block] = []
    tool_names: Dict[str, Tuple[str, dict]] = {}
    seen_calls = set()
    call = 0
    prev_prompt = 0
    peak = 0
    compaction_at: List[int] = []
    model = None
    with open(path, "rb") as f:
        for raw in f:
            try:
                d = json.loads(raw)
            except ValueError:
                continue
            t = d.get("type")
            m = d.get("message") or {}
            content = m.get("content")
            if t == "system" and d.get("subtype") == "compact_boundary":
                compaction_at.append(call)
                continue
            if t == "assistant":
                for part in content if isinstance(content, list) else []:
                    if isinstance(part, dict) and part.get("type") == "tool_use":
                        tool_names[part.get("id", "")] = (part.get("name") or "?", part.get("input") or {})
                usage = m.get("usage")
                key = (m.get("id"), d.get("requestId"))
                if not usage or key in seen_calls or m.get("model") == "<synthetic>":
                    continue
                seen_calls.add(key)
                model = m.get("model") or model
                prompt = (usage.get("input_tokens") or 0) + (usage.get("cache_creation_input_tokens") or 0) + (usage.get("cache_read_input_tokens") or 0)
                output = usage.get("output_tokens") or 0
                peak = max(peak, prompt)
                # Everything that arrived since the previous call (its own reply included) is
                # what this prompt grew by. Split that growth across the arrivals by estimated size.
                added = max(prompt - prev_prompt, 0) if call else prompt
                if not call:  # the first prompt is mostly the system prompt, tools and CLAUDE.md
                    pending.append(Block(SYSTEM_KIND, "system prompt, tools, CLAUDE.md", 0,
                                         max(prompt - sum(b.est for b in pending), 0)))
                est_sum = sum(b.est for b in pending)
                for b in pending:
                    b.tokens = added * b.est / est_sum if est_sum else 0.0
                    b.call = call
                blocks.extend(pending)
                # The reply is re-sent with the next call; thinking is mostly stripped by then,
                # so its true weight comes out of the next calibration, not from output_tokens.
                pending = [Block(ASSISTANT_KIND, "text, tool calls (and thinking within a turn)", call, output)]
                prev_prompt = prompt
                call += 1
                continue
            if t != "user":
                continue
            if d.get("isCompactSummary"):
                compaction_at.append(call)
            if isinstance(content, str):
                pending.append(Block(USER_KIND, content.strip().splitlines()[0][:48] if content.strip() else "", call, len(content) / CHARS_PER_TOKEN))
                continue
            for part in content if isinstance(content, list) else []:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "tool_result":
                    name, inp = tool_names.get(part.get("tool_use_id", ""), ("?", {}))
                    est, image = _content_est(part.get("content"))
                    pending.append(Block(name, _label(name, inp), call, est, is_image=image))
                elif part.get("type") == "text":
                    text = part.get("text") or ""
                    pending.append(Block(USER_KIND, text.strip().splitlines()[0][:48] if text.strip() else "", call, len(text) / CHARS_PER_TOKEN))
    price = price or price_for(model)
    r_eff = price.read_mult + miss_rate * (price.write_1h_mult - price.read_mult)
    ends = sorted(set(compaction_at)) + [call]
    for b in blocks:
        end = next(e for e in ends if e >= b.call + 1) if ends else call
        b.carried = max(end - b.call - 1, 0)
        b.carry_cost = b.tokens * b.carried * r_eff
        b.carry_usd = price.usd(b.carry_cost) or 0.0
    sid = os.path.splitext(os.path.basename(path))[0]
    return Diet(sid, call, peak, blocks, len(set(compaction_at)))


def aggregate(transcripts: Iterable[Transcript], miss_rate: float = 0.02) -> Tuple[Dict[str, dict], List[Tuple[str, Block]], int]:
    """Per-kind totals and the costliest single blocks across many sessions."""
    totals: Dict[str, dict] = {}
    top: List[Tuple[str, Block]] = []
    sessions = 0
    for t in transcripts:
        if t.is_subagent or len(t.steps) < 5:
            continue
        sessions += 1
        d = read_diet(t.path, miss_rate=miss_rate)
        for kind, v in d.by_kind().items():
            agg = totals.setdefault(kind, {"count": 0, "tokens": 0.0, "carry_usd": 0.0, "images": 0, "max": 0.0})
            for k in ("count", "tokens", "carry_usd", "images"):
                agg[k] += v[k]
            agg["max"] = max(agg["max"], v["max"])
        top.extend((d.session_id, b) for b in sorted((b for b in d.blocks if b.kind != SYSTEM_KIND),
                                                       key=lambda b: -b.carry_usd)[:10])
    top.sort(key=lambda sb: -sb[1].carry_usd)
    return dict(sorted(totals.items(), key=lambda kv: -kv[1]["carry_usd"])), top[:10], sessions


TIPS = {
    "Read": "Read with `offset`/`limit` or grep for the part you need; or let an Explore subagent read and report, "
            "its transcript stays out of yours.",
    "Bash": "Pipe through `tail -n 40` / `head`, redirect long logs to a file and `kvleak pointer` it, and run long "
            "jobs in the background so their output is not pasted back.",
    USER_KIND: "Pasted text is carried on every call. Save it to a file and reference the path; the model reads the "
               "part it needs when it needs it.",
    ASSISTANT_KIND: "The replies themselves are re-sent on every later call. Ask for terse answers in tool loops, keep "
                    "reports in files, and lower `/effort` for routine steps.",
    "Agent": "Subagent reports are small; the cost is in how many you run. Reuse one with SendMessage instead of "
             "spawning again.",
    "WebFetch": "Ask the fetch for the one fact you need; a page summary is much smaller than the page.",
    "WebSearch": "Narrow the query or ask for fewer results; each result set lands in the context whole.",
    SYSTEM_KIND: "Every session and subagent starts by paying for this. Trim CLAUDE.md, drop MCP servers you do not "
                 "use, and keep tool schemas deferred (ENABLE_TOOL_SEARCH).",
}


def tips(kinds: Dict[str, dict], limit: int = 3) -> List[str]:
    """One tip per dominant source (≥15% of the context), biggest first."""
    total = sum(v["tokens"] for v in kinds.values()) or 1.0
    out = []
    for kind, v in kinds.items():
        if len(out) >= limit or v["tokens"] / total < 0.15:
            continue
        text_tokens = v["tokens"] - v["images"] * IMAGE_MAX_TOKENS
        if kind == "Read" and text_tokens / total < 0.15:
            continue  # mostly images, and those are already capped by the API
        tip = TIPS.get(kind) or "Single results up to {} tokens from {}. Ask for summaries or counts; cap MCP output " \
                                "with MAX_MCP_OUTPUT_TOKENS.".format(int(v["max"]), kind)
        out.append("{} is {:.0%} of what entered the context. {}".format(
            kind[0].upper() + kind[1:] + (" results" if kind not in PLAIN_KINDS else ""),
            v["tokens"] / total, tip))
    if not out:
        out.append("No single source dominates. The remaining lever is compaction: see `kvleak tune`.")
    return out
