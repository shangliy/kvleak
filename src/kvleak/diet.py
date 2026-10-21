"""What filled the context, and what each piece cost to carry.

Compaction and TTLs treat the context as a blob. This module opens it up: every
tool result, user message, thinking block, tool call and reply in a Claude Code
transcript is a block that entered the context at some call and was re-read on
every call after it, until a compaction dropped it. The biggest blocks are
usually not what people expect (whole files), but the model's own output: the
scripts it sends to Bash, the files it writes, and on current models the
thinking it keeps across turns.

Token counts are calibrated against the API's own usage numbers: the tokens a
call added to the prompt (from `usage`) are split across the blocks that arrived
since the previous call. Thinking is exact (`output_tokens_details`); the rest
is estimated from text length, images by pixel count like the API prices them.
"""

from __future__ import annotations

import base64
import json
import os
import struct
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

from .pricing import Price, price_for
from .sources import Transcript

CHARS_PER_TOKEN = 4.0
IMAGE_MAX_TOKENS = 1_600  # the API downsizes images to ~1.15 MP; tokens ≈ pixels / 750
USER_KIND = "your messages"
TEXT_KIND = "assistant text"
THINKING_KIND = "thinking"
SYSTEM_KIND = "system prompt & tools"
PLAIN_KINDS = (USER_KIND, TEXT_KIND, THINKING_KIND, SYSTEM_KIND)  # everything else is "<Tool> results"
INPUT_SUFFIX = " inputs"  # "Bash inputs": what the model sent to the tool
LONG_TURN = 20  # calls; a turn this long is a loop worth moving to a subagent
SUBAGENT_START = 20_000  # context a fresh subagent starts with
MECHANICAL_TOOLS = {"Bash", "Read", "Grep", "Glob", "LS"}  # steps a script could take without the model
MECHANICAL_THINKING = 200  # tokens; less than this and the step was not a decision
MECHANICAL_TEXT = 300  # chars of reply
CHAIN_MIN = 3  # consecutive mechanical calls that would have been one script


@dataclass
class Block:
    kind: str
    label: str  # what it was: file path, command, first words
    call: int  # index of the call that first sent it
    est: float  # estimated tokens before calibration
    tokens: float = 0.0  # after calibration
    carried: int = 0  # calls that re-read it
    carry_cost: float = 0.0  # base-equivalent tokens
    carry_usd: float = 0.0
    entry_usd: float = 0.0  # what it cost the call that first sent it (a cache write)
    is_image: bool = False
    exact: bool = False  # est comes from usage, not from text length


@dataclass
class Turn:
    """One user request and every call made before the next one."""

    start: int  # first call index
    calls: int
    context: int  # prompt size at the first call
    history_usd: float = 0.0  # what re-reading that starting context on every call cost
    subagent_saving_usd: float = 0.0  # had the loop run in a fresh subagent instead


@dataclass
class Call:
    """One billed model call: what it decided and what it asked for."""

    index: int
    turn: int
    context: int
    thinking: int = 0
    text_chars: int = 0
    tools: List[Tuple[str, str]] = field(default_factory=list)  # (tool, label)

    @property
    def mechanical(self) -> bool:
        return bool(self.tools) and all(t in MECHANICAL_TOOLS for t, _ in self.tools) \
            and self.thinking < MECHANICAL_THINKING and self.text_chars < MECHANICAL_TEXT


@dataclass
class Chain:
    """Consecutive mechanical calls in one turn: a loop the model ran one step at a time."""

    start: int
    calls: int
    context: int
    labels: List[str]
    saving_usd: float = 0.0  # had the chain been one script: (calls − 1) re-reads of the context


@dataclass
class Diet:
    session_id: str
    calls: int
    peak: int  # largest prompt seen
    blocks: List[Block] = field(default_factory=list)
    turns: List[Turn] = field(default_factory=list)
    compactions: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0
    effort: Dict[Tuple[str, str], List[int]] = field(default_factory=dict)  # (model, effort) -> [calls, thinking]
    call_list: List[Call] = field(default_factory=list)
    chains: List[Chain] = field(default_factory=list)

    def by_kind(self) -> Dict[str, dict]:
        out: Dict[str, dict] = {}
        for b in self.blocks:
            d = out.setdefault(b.kind, {"count": 0, "tokens": 0.0, "carry_usd": 0.0, "entry_usd": 0.0, "images": 0, "max": 0.0})
            d["count"] += 1
            d["tokens"] += b.tokens
            d["carry_usd"] += b.carry_usd
            d["entry_usd"] += b.entry_usd
            d["images"] += int(b.is_image)
            d["max"] = max(d["max"], b.tokens)
        return dict(sorted(out.items(), key=lambda kv: -kv[1]["carry_usd"]))

    @property
    def total_tokens(self) -> float:
        return sum(b.tokens for b in self.blocks)

    @property
    def carry_usd(self) -> float:
        return sum(b.carry_usd for b in self.blocks)

    @property
    def long_turns(self) -> List[Turn]:
        return sorted((t for t in self.turns if t.calls >= LONG_TURN), key=lambda t: -t.history_usd)


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


def _first_line(text: str) -> str:
    text = text.strip()
    return text.splitlines()[0][:48] if text else ""


def read_diet(path: str, price: Optional[Price] = None, miss_rate: float = 0.02) -> Diet:
    pending: List[Block] = []
    blocks: List[Block] = []
    tool_names: Dict[str, Tuple[str, dict]] = {}
    seen_calls = set()
    seen_messages = set()
    call = 0
    prev_prompt = 0
    peak = 0
    compaction_at: List[int] = []
    turns: List[Turn] = []
    turn: Optional[Turn] = None
    model = None
    out_tokens = think_tokens = 0
    effort: Dict[Tuple[str, str], List[int]] = defaultdict(lambda: [0, 0])
    calls: List[Call] = []
    call_of_msg: Dict[str, Call] = {}
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
                usage = m.get("usage")
                key = (m.get("id"), d.get("requestId"))
                is_call = bool(usage) and key not in seen_calls and m.get("model") != "<synthetic>"
                if is_call:
                    prompt_now = (usage.get("input_tokens") or 0) + (usage.get("cache_creation_input_tokens") or 0) + (usage.get("cache_read_input_tokens") or 0)
                    this_call = Call(call, len(turns) if turn is None else len(turns) - 1, prompt_now,
                                     (usage.get("output_tokens_details") or {}).get("thinking_tokens") or 0)
                    calls.append(this_call)
                    call_of_msg[m.get("id") or ""] = this_call
                meta = call_of_msg.get(m.get("id") or "")
                # Claude Code writes one line per content block; the reply's blocks enter the
                # context with the *next* call, so they go to `pending` like a tool result.
                for part in content if isinstance(content, list) else []:
                    if not isinstance(part, dict):
                        continue
                    if part.get("type") == "tool_use":
                        name, inp = part.get("name") or "?", part.get("input") or {}
                        tool_names[part.get("id", "")] = (name, inp)
                        pending.append(Block(name + INPUT_SUFFIX, _label(name, inp), call, len(json.dumps(inp)) / CHARS_PER_TOKEN))
                        if meta:
                            meta.tools.append((name, _label(name, inp)))
                    elif part.get("type") == "text":
                        pending.append(Block(TEXT_KIND, _first_line(part.get("text") or ""), call, len(part.get("text") or "") / CHARS_PER_TOKEN))
                        if meta:
                            meta.text_chars += len(part.get("text") or "")
                if not is_call:
                    continue
                seen_calls.add(key)
                model = m.get("model") or model
                prompt = (usage.get("input_tokens") or 0) + (usage.get("cache_creation_input_tokens") or 0) + (usage.get("cache_read_input_tokens") or 0)
                output = usage.get("output_tokens") or 0
                thinking = (usage.get("output_tokens_details") or {}).get("thinking_tokens") or 0
                out_tokens += output
                think_tokens += thinking
                e = effort[(model or "?", d.get("effort") or "?")]
                e[0] += 1
                e[1] += thinking
                peak = max(peak, prompt)
                if turn is None:
                    turn = Turn(call, 0, prompt)
                    turns.append(turn)
                turn.calls += 1
                # Everything that arrived since the previous call is what this prompt grew by.
                # Thinking is known exactly; the rest shares what remains, by estimated size.
                added = max(prompt - prev_prompt, 0) if call else prompt
                if not call:  # the first prompt is mostly the system prompt, tools and CLAUDE.md
                    pending.append(Block(SYSTEM_KIND, "system prompt, tools, CLAUDE.md", 0,
                                         max(prompt - sum(b.est for b in pending), 0)))
                exact = sum(b.est for b in pending if b.exact)
                rest = max(added - exact, 0)
                est_sum = sum(b.est for b in pending if not b.exact)
                for b in pending:
                    b.tokens = b.est if b.exact else (rest * b.est / est_sum if est_sum else 0.0)
                    b.call = call
                blocks.extend(pending)
                pending = []
                if thinking and m.get("id") not in seen_messages:  # kept across turns on current models
                    pending.append(Block(THINKING_KIND, "kept in context on this model", call, float(thinking), exact=True))
                seen_messages.add(m.get("id"))
                prev_prompt = prompt
                call += 1
                continue
            if t != "user":
                continue
            if d.get("isCompactSummary"):
                compaction_at.append(call)
            is_tool_result = isinstance(content, list) and any(
                isinstance(p, dict) and p.get("type") == "tool_result" for p in content)
            if not is_tool_result and not d.get("isMeta"):
                turn = None  # a new request from the user starts a new turn
            if isinstance(content, str):
                pending.append(Block(USER_KIND, _first_line(content), call, len(content) / CHARS_PER_TOKEN))
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
                    pending.append(Block(USER_KIND, _first_line(text), call, len(text) / CHARS_PER_TOKEN))
    price = price or price_for(model)
    r_eff = price.read_mult + miss_rate * (price.write_1h_mult - price.read_mult)
    ends = sorted(set(compaction_at)) + [call]
    for b in blocks:
        end = next(e for e in ends if e >= b.call + 1) if ends else call
        b.carried = max(end - b.call - 1, 0)
        b.carry_cost = b.tokens * b.carried * r_eff
        b.carry_usd = price.usd(b.carry_cost) or 0.0
        b.entry_usd = price.usd(b.tokens * price.write_1h_mult) or 0.0
    for tn in turns:
        tn.history_usd = price.usd(tn.calls * tn.context * r_eff) or 0.0
        tn.subagent_saving_usd = price.usd(tn.calls * max(tn.context - SUBAGENT_START, 0) * r_eff) or 0.0 if tn.calls >= LONG_TURN else 0.0
    sid = os.path.splitext(os.path.basename(path))[0]
    return Diet(sid, call, peak, blocks, turns, len(set(compaction_at)), out_tokens, think_tokens, dict(effort),
                calls, find_chains(calls, price, r_eff))


def find_chains(calls: List[Call], price: Price, r_eff: float) -> List[Chain]:
    """Runs of ≥CHAIN_MIN mechanical calls in one turn, priced as the re-reads a script would have skipped."""
    chains: List[Chain] = []
    run: List[Call] = []

    def flush():
        if len(run) >= CHAIN_MIN:
            first = run[0]
            labels = [lbl for c in run for _, lbl in c.tools][:6]
            chains.append(Chain(first.index, len(run), first.context, labels,
                                price.usd((len(run) - 1) * first.context * r_eff) or 0.0))
        run.clear()

    for c in calls:
        if c.mechanical and (not run or run[-1].turn == c.turn):
            run.append(c)
        else:
            flush()
            if c.mechanical:
                run.append(c)
    flush()
    return sorted(chains, key=lambda ch: -ch.saving_usd)


@dataclass
class Aggregate:
    kinds: Dict[str, dict]
    top: List[Tuple[str, Block]]
    sessions: int = 0
    calls: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0
    long_turns: int = 0
    long_turn_calls: int = 0
    history_usd: float = 0.0
    subagent_saving_usd: float = 0.0
    effort: Dict[Tuple[str, str], List[int]] = field(default_factory=dict)
    bash_blocks: List[Tuple[str, Block]] = field(default_factory=list)  # every Bash result, for cap backtests
    turns: int = 0
    chains: int = 0
    chain_calls: int = 0
    chain_saving_usd: float = 0.0
    chain_examples: List[Tuple[str, Chain]] = field(default_factory=list)
    carry_usd: float = 0.0


def aggregate(transcripts: Iterable[Transcript], miss_rate: float = 0.02) -> Aggregate:
    """Per-kind totals, the costliest single blocks and the turn structure across many sessions."""
    agg = Aggregate({}, [])
    for t in transcripts:
        if t.is_subagent or len(t.steps) < 5 or t.source != "claude":
            continue
        d = read_diet(t.path, miss_rate=miss_rate)
        agg.sessions += 1
        agg.calls += d.calls
        agg.output_tokens += d.output_tokens
        agg.thinking_tokens += d.thinking_tokens
        for kind, v in d.by_kind().items():
            a = agg.kinds.setdefault(kind, {"count": 0, "tokens": 0.0, "carry_usd": 0.0, "entry_usd": 0.0, "images": 0, "max": 0.0})
            for k in ("count", "tokens", "carry_usd", "entry_usd", "images"):
                a[k] += v[k]
            a["max"] = max(a["max"], v["max"])
        agg.bash_blocks.extend((d.session_id, b) for b in d.blocks if b.kind == "Bash")
        agg.top.extend((d.session_id, b) for b in sorted((b for b in d.blocks if b.kind != SYSTEM_KIND),
                                                          key=lambda b: -b.carry_usd)[:10])
        for tn in d.long_turns:
            agg.long_turns += 1
            agg.long_turn_calls += tn.calls
            agg.history_usd += tn.history_usd
            agg.subagent_saving_usd += tn.subagent_saving_usd
        for k, (n, th) in d.effort.items():
            e = agg.effort.setdefault(k, [0, 0])
            e[0] += n
            e[1] += th
        agg.turns += len(d.turns)
        agg.carry_usd += d.carry_usd
        for ch in d.chains:
            agg.chains += 1
            agg.chain_calls += ch.calls
            agg.chain_saving_usd += ch.saving_usd
        agg.chain_examples.extend((d.session_id, ch) for ch in d.chains[:3])
    agg.kinds = dict(sorted(agg.kinds.items(), key=lambda kv: -kv[1]["carry_usd"]))
    agg.top.sort(key=lambda sb: -sb[1].carry_usd)
    agg.top = agg.top[:10]
    agg.chain_examples.sort(key=lambda sc: -sc[1].saving_usd)
    agg.chain_examples = agg.chain_examples[:5]
    return agg


TIPS = {
    "Read": "Read with `offset`/`limit` or grep for the part you need; or let an Explore subagent read and report, "
            "its transcript stays out of yours.",
    "Bash": "Pipe through `tail -n 40` / `head`, redirect long logs to a file and `kvleak pointer` it, and run long "
            "jobs in the background so their output is not pasted back.",
    "Bash" + INPUT_SUFFIX: "Every inline script or heredoc the model sends to Bash is re-read on every later call. Put "
                           "multi-line scripts in a file once and run the file; iterate with Edit, not by resending.",
    "Write" + INPUT_SUFFIX: "A Write carries the whole file; an Edit carries only the change. Generate big data files "
                            "with a script instead of writing them out literally.",
    USER_KIND: "Pasted text is carried on every call. Save it to a file and reference the path; the model reads the "
               "part it needs when it needs it.",
    TEXT_KIND: "Replies are re-sent on every later call. Ask for terse answers in tool loops and keep reports in files.",
    THINKING_KIND: "Current models keep every turn's thinking in context until a compaction. Lower `/effort` for "
                   "routine work, give routine skills and subagents `effort: low` in their frontmatter, and let "
                   "`kvleak tune` set the auto-compact window.",
    SYSTEM_KIND: "Every session and subagent starts by paying for this. Trim CLAUDE.md, drop MCP servers you do not "
                 "use, and keep tool schemas deferred (ENABLE_TOOL_SEARCH).",
    "Agent": "Subagent reports are small; the cost is in how many you run. Reuse one with SendMessage instead of "
             "spawning again.",
    "WebFetch": "Ask the fetch for the one fact you need; a page summary is much smaller than the page.",
    "WebSearch": "Narrow the query or ask for fewer results; each result set lands in the context whole.",
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
        out.append("{} is {:.0%} of what entered the context. {}".format(kind_label(kind), v["tokens"] / total, tip))
    if not out:
        out.append("No single source dominates. The remaining lever is compaction: see `kvleak tune`.")
    return out


def kind_label(kind: str) -> str:
    if kind in PLAIN_KINDS or kind.endswith(INPUT_SUFFIX):
        return kind[0].upper() + kind[1:]
    return kind + " results"
