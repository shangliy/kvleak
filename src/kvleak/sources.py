"""Transcript readers.

Each reader turns one log file into a list of `Step`s, one per billed model
call, in chronological order. Two formats are supported out of the box:

* ``claude``  - Claude Code transcripts (``~/.claude/projects/**/*.jsonl``),
  including subagent transcripts under ``<session>/subagents/``.
* ``generic`` - any JSONL where each line carries usage counters. Common key
  spellings are auto-detected (Anthropic, OpenAI, and agent-framework style),
  see ``_generic_step``.

Adding a new agent harness = writing one function that yields `Step`s.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator, List, Optional


@dataclass
class Step:
    ts: Optional[datetime]
    model: Optional[str]
    input: int = 0  # uncached input, billed 1.0x
    write_5m: int = 0  # cache writes with 5-minute TTL
    write_1h: int = 0  # cache writes with 1-hour TTL
    read: int = 0  # cache hits
    output: int = 0
    after_compaction: bool = False  # history was rewritten just before this call

    @property
    def uncached(self) -> int:
        return self.input + self.write_5m + self.write_1h

    @property
    def prompt(self) -> int:
        return self.uncached + self.read


@dataclass
class Transcript:
    path: str
    session_id: str
    project: str
    is_subagent: bool
    steps: List[Step]
    source: str = "claude"  # which tool wrote it: claude, codex, gemini, opencode, generic


def parse_ts(value) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        if value > 1e12:  # milliseconds
            value /= 1000.0
        return datetime.fromtimestamp(value, tz=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _int(v) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


# --------------------------------------------------------------------------- claude


def default_claude_root() -> Path:
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(base) / "projects" if base else Path.home() / ".claude" / "projects"


def read_claude(path: str, tail_bytes: Optional[int] = None) -> Transcript:
    """Parse a Claude Code transcript; with `tail_bytes`, only its last N bytes."""
    steps: List[Step] = []
    seen = set()
    pending_compaction = False
    cwd = None
    sidechain = "/subagents/" in path.replace(os.sep, "/")
    session_id = Path(path).stem
    with open(path, "rb") as fb:
        if tail_bytes is not None:
            fb.seek(max(os.path.getsize(path) - tail_bytes, 0))
            if fb.tell():
                fb.readline()  # drop the partial first line
        f = (raw.decode("utf-8", errors="replace") for raw in fb)
        for line in f:
            # Cheap prefilter: most lines are tool results / user text.
            if '"usage"' not in line and "compact" not in line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if obj.get("subtype") == "compact_boundary" or obj.get("isCompactSummary"):
                pending_compaction = True
                continue
            if obj.get("type") != "assistant":
                continue
            msg = obj.get("message") or {}
            usage = msg.get("usage")
            if not usage:
                continue
            # Claude Code writes one line per content block; usage repeats. Dedupe.
            key = (msg.get("id"), obj.get("requestId"))
            if key != (None, None):
                if key in seen:
                    continue
                seen.add(key)
            if obj.get("isSidechain"):
                sidechain = True
            if cwd is None and obj.get("cwd"):
                cwd = obj["cwd"]
            session_id = obj.get("sessionId") or session_id
            created = _int(usage.get("cache_creation_input_tokens"))
            breakdown = usage.get("cache_creation") or {}
            # Older transcripts lack the TTL breakdown; treat unlabelled writes as 5m.
            w1h = min(_int(breakdown.get("ephemeral_1h_input_tokens")), created)
            w5m = created - w1h
            step = Step(
                ts=parse_ts(obj.get("timestamp")),
                model=msg.get("model"),
                input=_int(usage.get("input_tokens")),
                write_5m=w5m,
                write_1h=w1h,
                read=_int(usage.get("cache_read_input_tokens")),
                output=_int(usage.get("output_tokens")),
                after_compaction=pending_compaction,
            )
            if step.prompt == 0 or step.model == "<synthetic>":
                continue
            pending_compaction = False
            steps.append(step)
    p = Path(path)
    if cwd:
        project = Path(cwd).name or cwd
    else:  # Claude Code encodes /home/me/src/app as -home-me-src-app
        project_dir = p.parent.parent.parent if p.parent.name == "subagents" else p.parent
        project = project_dir.name
    return Transcript(path, session_id, project, sidechain, steps)


# -------------------------------------------------------------------------- generic

_INPUT_KEYS = ("input_tokens", "prompt_tokens", "promptTokenCount", "uncached_input_tokens")
_READ_KEYS = ("cache_read_input_tokens", "cache_read_tokens", "cached_tokens", "cachedContentTokenCount")
_WRITE_KEYS = ("cache_creation_input_tokens", "cache_write_tokens")
_OUTPUT_KEYS = ("output_tokens", "completion_tokens", "candidatesTokenCount")
_TS_KEYS = ("timestamp", "created_at", "createdAt", "time", "ts", "_t_start", "_t_end")


def _first(d: dict, keys) -> Optional[object]:
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


def _generic_step(obj: dict) -> Optional[Step]:
    usage = obj
    for k in ("usage", "usageMetadata", "token_usage"):
        if isinstance(obj.get(k), dict):
            usage = obj[k]
            break
    if isinstance(obj.get("message"), dict) and isinstance(obj["message"].get("usage"), dict):
        usage = obj["message"]["usage"]
    inp = _int(_first(usage, _INPUT_KEYS))
    read = _int(_first(usage, _READ_KEYS))
    details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
    openai_style = "prompt_tokens" in usage or "cached_tokens" in details or "promptTokenCount" in usage
    if not read and isinstance(details, dict):
        read = _int(details.get("cached_tokens"))
    if openai_style:  # OpenAI / Gemini report cached tokens *inside* prompt tokens
        inp = max(inp - read, 0)
    write = _int(_first(usage, _WRITE_KEYS))
    if inp + read + write == 0:
        return None
    breakdown = usage.get("cache_creation") if isinstance(usage.get("cache_creation"), dict) else {}
    w1h = min(_int(breakdown.get("ephemeral_1h_input_tokens")), write)
    w5m = write - w1h
    model_usage = obj.get("modelUsage") if isinstance(obj.get("modelUsage"), dict) else {}
    model = (
        obj.get("model")
        or (obj.get("message") or {}).get("model")
        or usage.get("model")
        or next(iter(model_usage), None)
        or (obj.get("_init") or {}).get("model")
    )
    compacted = bool(obj.get("_compact"))
    return Step(
        ts=parse_ts(_first(obj, _TS_KEYS)),
        model=model,
        input=inp,
        write_5m=w5m,
        write_1h=w1h,
        read=read,
        output=_int(_first(usage, _OUTPUT_KEYS)),
        after_compaction=compacted,
    )


def read_generic(path: str) -> Transcript:
    steps = []
    p = Path(path)
    session_id = p.stem
    raw = open(path, "r", encoding="utf-8", errors="replace").read()
    for line in raw.splitlines():
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            session_id = obj.get("session_id") or obj.get("sessionId") or session_id
            step = _generic_step(obj)
            if step:
                steps.append(step)
    if not steps:
        try:
            doc = json.loads(raw)
        except ValueError:
            doc = None
        items = doc if isinstance(doc, list) else [doc] if isinstance(doc, dict) else []
        for obj in items:
            if isinstance(obj, dict):
                session_id = obj.get("session_id") or obj.get("sessionId") or session_id
                step = _generic_step(obj)
                if step:
                    steps.append(step)
    return Transcript(path, str(session_id), p.parent.name, False, steps, "generic")


# ------------------------------------------------------------------------- discovery

SUFFIXES = (".jsonl", ".json", ".db")


def discover(paths: Iterable[str], since: Optional[float] = None) -> Iterator[str]:
    """Yield session files (.jsonl, .json, .db) under the given files/dirs, optionally newer than `since`."""
    for root in paths:
        root = os.path.expanduser(root)
        if os.path.isfile(root):
            yield root
            continue
        for dirpath, _, files in os.walk(root):
            for name in files:
                if not name.endswith(SUFFIXES):
                    continue
                full = os.path.join(dirpath, name)
                if since is not None:
                    try:
                        if os.path.getmtime(full) < since:
                            continue
                    except OSError:
                        continue
                yield full


def detect_format(path: str) -> str:
    """'claude', 'codex', 'gemini', 'opencode', 'generic', or 'skip' for files that are not sessions."""
    from .readers import is_codex, is_gemini_path, is_opencode_path

    if path.endswith(".db"):
        return "opencode" if is_opencode_path(path) else "skip"
    if is_gemini_path(path):
        return "gemini"
    if path.endswith(".json"):
        try:
            head = open(path, "r", encoding="utf-8", errors="replace").read(16_384)
        except OSError:
            return "skip"
        if '"usage"' in head and any(f'"{k}"' in head for k in _INPUT_KEYS + _READ_KEYS + _WRITE_KEYS):
            return "generic"
        return "skip"
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for i, line in zip(range(50), f):
                if i == 0 and is_codex(line):
                    return "codex"
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if isinstance(obj, dict) and "sessionId" in obj and obj.get("type") in ("user", "assistant", "system"):
                    return "claude"
    except OSError:
        pass
    return "generic"


def load_many(path: str, fmt: str = "auto") -> List[Transcript]:
    """Every transcript in a file: one for most formats, one per session for an OpenCode database."""
    from .readers import read_codex, read_gemini, read_opencode

    if fmt == "auto":
        fmt = detect_format(path)
    if fmt == "skip":
        return []
    if fmt == "opencode":
        return read_opencode(path)
    reader = {"claude": read_claude, "codex": read_codex, "gemini": read_gemini}.get(fmt, read_generic)
    return [reader(path)]


def load(path: str, fmt: str = "auto") -> Transcript:
    found = load_many(path, fmt)
    return found[0] if found else Transcript(path, Path(path).stem, Path(path).parent.name, False, [], "generic")
