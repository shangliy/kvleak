"""Claude Code hooks: keep bulk out of the context, and make compaction lossless.

Two ideas borrowed from REPL-style harnesses (Prime Agent's "context as a
variable"): a tool's full output should live in a variable the model can query,
not in the prompt; and the history a compaction drops should stay retrievable
instead of gone.

* ``PostToolUse`` (Bash): when a result is longer than ``max_chars``, the full
  output is written to ``~/.cache/kvleak/outputs/<session>/<tool_use_id>.txt``
  and the model sees a condensed view instead: the head, the tail, every line
  that looks like an error, repeated lines collapsed, and the path to the rest.
* ``PreCompact``: every tool result, user message and reply in the transcript
  is appended to ``~/.cache/kvleak/history/<session>/history.jsonl`` before the
  summary replaces them.
* ``PostCompact``: the summary itself is saved next to it.
* ``SessionStart`` (``compact``): a short map of the archived history and the
  ``kvleak recall`` command are injected, so the model can grep what it lost.

Hooks must never break a session: every entry point swallows its own errors
and prints nothing on failure.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

DEFAULT_MAX_CHARS = 8_000
FLAG_RE = re.compile(r"(?i)(error|fail|exception|traceback|warning|panic|fatal|assert|denied|not found|no such file|"
                     r"cannot|can't|unexpected|timed out|timeout)|^E\s|^\s*at .+\(.+:\d+\)")
FLAG_FOLLOW = 2  # lines kept after a flagged one (the body of a traceback)
MAX_FLAGGED = 40
HEAD_SHARE, FLAG_SHARE = 0.35, 0.20  # of max_chars; the tail gets the rest
INJECT_MAX_CHARS = 6_000  # Claude Code caps SessionStart additionalContext at 10k
HOOK_EVENTS = ("post-tool", "pre-compact", "post-compact", "session-start")


# ------------------------------------------------------------------------ paths


def cache_root() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "kvleak"


def outputs_dir(session_id: str) -> Path:
    return cache_root() / "outputs" / _safe(session_id)


def history_dir(session_id: str) -> Path:
    return cache_root() / "history" / _safe(session_id)


def _safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", s or "unknown")[:80]


# --------------------------------------------------------------------- condense


def _collapse_repeats(lines: List[str]) -> List[str]:
    out: List[str] = []
    run = 0
    for ln in lines:
        if out and ln == out[-1].rsplit("   (×", 1)[0] and ln.strip():
            run += 1
            out[-1] = "{}   (×{})".format(ln, run + 1)
        else:
            run = 0
            out.append(ln)
    return out


def _lines(text: str) -> List[str]:
    # Progress bars rewrite the line with \r; keep what was finally shown.
    return [ln.rsplit("\r", 1)[-1] for ln in text.splitlines()]


def condense(text: str, max_chars: int = DEFAULT_MAX_CHARS, path: Optional[str] = None) -> Tuple[str, Dict[str, int]]:
    """A view of `text` that fits in `max_chars`: head, flagged lines, tail, and where the rest is.

    Returns the view and {"lines", "chars", "flagged", "omitted"}; the view is the
    input itself when it already fits.
    """
    stats = {"lines": text.count("\n") + 1, "chars": len(text), "flagged": 0, "omitted": 0}
    if len(text) <= max_chars:
        return text, stats
    lines = _collapse_repeats(_lines(text))
    head_budget, flag_budget = int(max_chars * HEAD_SHARE), int(max_chars * FLAG_SHARE)
    tail_budget = max_chars - head_budget - flag_budget - 400  # room for the notes

    head: List[str] = []
    used = 0
    for ln in lines:
        if used + len(ln) + 1 > head_budget:
            break
        head.append(ln)
        used += len(ln) + 1
    tail: List[str] = []
    used = 0
    for ln in reversed(lines[len(head):]):
        if used + len(ln) + 1 > tail_budget:
            break
        tail.insert(0, ln)
        used += len(ln) + 1
    middle_start, middle_end = len(head), len(lines) - len(tail)
    flagged: List[str] = []
    used = 0
    follow = 0
    for i in range(middle_start, middle_end):
        ln = lines[i]
        hit = bool(FLAG_RE.search(ln))
        if not hit and not (follow and ln.strip()):
            follow = 0
            continue
        follow = FLAG_FOLLOW if hit else follow - 1
        entry = "L{}: {}".format(i + 1, ln.strip()[:300])
        if used + len(entry) + 1 > flag_budget or len(flagged) >= MAX_FLAGGED:
            break
        flagged.append(entry)
        used += len(entry) + 1
    omitted = middle_end - middle_start
    stats["flagged"], stats["omitted"] = len(flagged), omitted
    where = " Full output: {}".format(path) if path else ""
    note = "… [kvleak: {} lines ({} chars) omitted here.{}{}]".format(
        omitted, len(text), " Lines that looked like errors are kept below." if flagged else "", where)
    parts = head + [note] + flagged + (["…"] if flagged and tail else []) + tail
    return "\n".join(parts), stats


# ------------------------------------------------------------------- post-tool


def _as_text(content) -> str:
    if isinstance(content, str):
        return content
    out = []
    for part in content or []:
        if isinstance(part, dict):
            if part.get("type") == "text":
                out.append(part.get("text") or "")
            elif part.get("type") == "image":
                out.append("[image]")
    return "\n".join(out)


def hook_post_tool(payload: dict, max_chars: int = DEFAULT_MAX_CHARS) -> Optional[dict]:
    """PostToolUse for Bash: archive long output, return `updatedToolOutput` with the condensed view."""
    if payload.get("tool_name") != "Bash":
        return None
    resp = payload.get("tool_response")
    if isinstance(resp, str):
        resp = {"stdout": resp, "stderr": "", "interrupted": False, "isImage": False}
    if not isinstance(resp, dict) or resp.get("isImage"):
        return None
    stdout, stderr = str(resp.get("stdout") or ""), str(resp.get("stderr") or "")
    if len(stdout) + len(stderr) <= max_chars:
        return None
    tool_id = _safe(payload.get("tool_use_id") or "tool-{}".format(int(time.time() * 1000)))
    d = outputs_dir(payload.get("session_id") or "unknown")
    d.mkdir(parents=True, exist_ok=True)
    path = d / (tool_id + ".txt")
    cmd = str((payload.get("tool_input") or {}).get("command") or "").strip()
    with open(path, "w", encoding="utf-8", errors="replace") as f:
        if cmd:
            f.write("$ {}\n".format(cmd))
        f.write(stdout)
        if stderr:
            f.write("\n--- stderr ---\n" + stderr)
    err_budget = min(len(stderr), max_chars // 3) if stderr else 0
    out_view, _ = condense(stdout, max(max_chars - err_budget, 1_000), str(path))
    err_view, _ = condense(stderr, max(err_budget, 500), str(path)) if stderr else ("", None)
    updated = dict(resp)
    updated["stdout"], updated["stderr"] = out_view, err_view
    return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "updatedToolOutput": updated}}


# ------------------------------------------------------------------ pre-compact


def _transcript_records(path: str) -> Iterator[dict]:
    """Every block that was in the context: tool results, user messages, replies and tool calls."""
    tool_names: Dict[str, Tuple[str, str]] = {}
    with open(path, "rb") as f:
        for raw in f:
            try:
                d = json.loads(raw)
            except ValueError:
                continue
            t = d.get("type")
            m = d.get("message") or {}
            content = m.get("content")
            ts = d.get("timestamp") or ""
            if t == "system" and d.get("subtype") == "compact_boundary":
                yield {"kind": "compaction", "ts": ts, "text": ""}
            elif t == "assistant":
                for part in content if isinstance(content, list) else []:
                    if not isinstance(part, dict):
                        continue
                    if part.get("type") == "tool_use":
                        name, inp = part.get("name") or "?", part.get("input") or {}
                        label = _label(inp) or name
                        tool_names[part.get("id", "")] = (name, label)
                        yield {"kind": "call", "ts": ts, "tool": name, "id": part.get("id"), "label": label,
                               "text": json.dumps(inp, ensure_ascii=False)}
                    elif part.get("type") == "text" and (part.get("text") or "").strip():
                        yield {"kind": "assistant", "ts": ts, "label": _first_line(part["text"]), "text": part["text"]}
            elif t == "user":
                if d.get("isCompactSummary"):
                    yield {"kind": "compaction", "ts": ts, "text": ""}
                    continue
                if isinstance(content, str):
                    if not d.get("isMeta") and content.strip():
                        yield {"kind": "user", "ts": ts, "label": _first_line(content), "text": content}
                    continue
                for part in content if isinstance(content, list) else []:
                    if not isinstance(part, dict):
                        continue
                    if part.get("type") == "tool_result":
                        name, label = tool_names.get(part.get("tool_use_id", ""), ("?", "?"))
                        yield {"kind": "result", "ts": ts, "tool": name, "id": part.get("tool_use_id"), "label": label,
                               "text": _as_text(part.get("content"))}
                    elif part.get("type") == "text" and not d.get("isMeta") and (part.get("text") or "").strip():
                        yield {"kind": "user", "ts": ts, "label": _first_line(part["text"]), "text": part["text"]}


def _label(inp: dict) -> str:
    for key in ("command", "file_path", "pattern", "description", "prompt", "query", "url", "skill"):
        v = inp.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip().splitlines()[0][:100]
    return ""


def _first_line(text: str) -> str:
    text = (text or "").strip()
    return text.splitlines()[0][:100] if text else ""


def archive(session_id: str, transcript_path: str) -> Tuple[Path, int]:
    """Append the records of `transcript_path` not archived yet; returns (history file, records added)."""
    d = history_dir(session_id)
    d.mkdir(parents=True, exist_ok=True)
    state_path, hist_path = d / "state.json", d / "history.jsonl"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    done = int(state.get("records", 0))
    outputs = outputs_dir(session_id)
    added = 0
    with open(hist_path, "a", encoding="utf-8") as out:
        for i, rec in enumerate(_transcript_records(transcript_path)):
            if i < done:
                continue
            rec["i"] = i
            if rec["kind"] == "result" and rec.get("id"):
                full = outputs / (_safe(rec["id"]) + ".txt")
                if full.exists():
                    rec["full"] = str(full)
            rec["chars"] = len(rec.get("text") or "")
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            added += 1
    state.update({"records": done + added, "transcript": transcript_path, "archived_at": time.time()})
    state_path.write_text(json.dumps(state), encoding="utf-8")
    return hist_path, added


def hook_pre_compact(payload: dict) -> None:
    path = payload.get("transcript_path")
    if path and os.path.isfile(path):
        archive(payload.get("session_id") or Path(path).stem, path)


def hook_post_compact(payload: dict) -> None:
    summary = payload.get("compact_summary")
    if not summary:
        return
    d = history_dir(payload.get("session_id") or "unknown")
    d.mkdir(parents=True, exist_ok=True)
    n = len(list(d.glob("summary-*.md"))) + 1
    (d / "summary-{}.md".format(n)).write_text(str(summary), encoding="utf-8")


# ---------------------------------------------------------------- session-start


def load_history(session_id: str) -> List[dict]:
    p = history_dir(session_id) / "history.jsonl"
    if not p.exists():
        return []
    out = []
    with open(p, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def history_map(records: List[dict], max_chars: int = INJECT_MAX_CHARS) -> str:
    """One line per user request with the record range and the tools used after it."""
    turns: List[Tuple[int, str, Dict[str, int], int]] = []
    for r in records:
        if r["kind"] == "user":
            turns.append((r["i"], r.get("label") or "", {}, 0))
        elif r["kind"] == "call" and turns:
            i, label, tools, n = turns[-1]
            tools[r.get("tool") or "?"] = tools.get(r.get("tool") or "?", 0) + 1
            turns[-1] = (i, label, tools, n + 1)
    lines = []
    for i, label, tools, n in turns:
        used = ", ".join("{}×{}".format(k, v) for k, v in sorted(tools.items(), key=lambda kv: -kv[1])[:4])
        lines.append("  #{} {}{}".format(i, label[:80], "  [{} calls: {}]".format(n, used) if n else ""))
    text = "\n".join(lines)
    if len(text) > max_chars:  # keep the most recent turns
        kept = []
        used = 0
        for ln in reversed(lines):
            if used + len(ln) + 1 > max_chars - 60:
                break
            kept.insert(0, ln)
            used += len(ln) + 1
        text = "  … ({} earlier requests not listed)\n".format(len(lines) - len(kept)) + "\n".join(kept)
    return text


def hook_session_start(payload: dict) -> Optional[dict]:
    if payload.get("source") != "compact":
        return None
    sid = payload.get("session_id") or ""
    records = load_history(sid)
    if not records:
        return None
    results = sum(1 for r in records if r["kind"] == "result")
    chars = sum(r.get("chars", 0) for r in records)
    text = (
        "[kvleak] The history before this compaction is archived, not lost: {} records ({} tool results, {:,} chars) in {}.\n"
        "Before re-running a command or re-reading a file to recover something you already had, search the archive:\n"
        "  kvleak recall \"<regex>\" --session {}      # matching lines with their record numbers\n"
        "  kvleak recall --show <record#> --session {}  # one record in full (pipe through head/sed -n)\n"
        "Requests in the archived history:\n{}"
    ).format(len(records), results, chars, history_dir(sid), sid[:8], sid[:8], history_map(records))
    return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text[:INJECT_MAX_CHARS + 3_000]}}


# ----------------------------------------------------------------------- recall


def archived_sessions() -> List[Path]:
    root = cache_root() / "history"
    if not root.exists():
        return []
    return sorted((p for p in root.iterdir() if (p / "history.jsonl").exists()), key=lambda p: -p.stat().st_mtime)


def resolve_session(prefix: Optional[str]) -> Optional[str]:
    sessions = archived_sessions()
    if not sessions:
        return None
    if not prefix:
        return sessions[0].name
    for p in sessions:
        if p.name.startswith(prefix):
            return p.name
    return None


def recall(session_id: str, query: Optional[str] = None, show: Optional[int] = None, limit: int = 40, context: int = 1) -> str:
    records = load_history(session_id)
    if show is not None:
        for r in records:
            if r.get("i") == show:
                head = "# record {} · {} · {}{}\n".format(r["i"], r["kind"], r.get("tool") or "", (" · " + r["label"]) if r.get("label") else "")
                body = r.get("text") or ""
                if r.get("full") and os.path.isfile(r["full"]):
                    body = Path(r["full"]).read_text(encoding="utf-8", errors="replace")
                return head + body
        return "kvleak: no record {} in session {}".format(show, session_id[:8])
    if not query:
        return history_map(records, max_chars=10**9) or "kvleak: nothing archived for {}".format(session_id[:8])
    rx = re.compile(query, re.IGNORECASE)
    out: List[str] = []
    hits = 0
    for r in records:
        text = r.get("text") or ""
        if r.get("full") and os.path.isfile(r["full"]):
            try:
                text = Path(r["full"]).read_text(encoding="utf-8", errors="replace")
            except OSError:
                pass
        lines = text.splitlines()
        matched = [i for i, ln in enumerate(lines) if rx.search(ln)] or ([0] if rx.search(r.get("label") or "") else [])
        if not matched:
            continue
        out.append("#{} {} {} {}".format(r["i"], r["kind"], r.get("tool") or "", (r.get("label") or "")[:80]).rstrip())
        shown = set()
        for i in matched[:5]:
            for j in range(max(i - context, 0), min(i + context + 1, len(lines))):
                if j in shown:
                    continue
                shown.add(j)
                out.append("    {}{}".format("> " if j == i else "  ", lines[j][:200]))
        hits += 1
        if hits >= limit:
            out.append("… more matches; narrow the query")
            break
    return "\n".join(out) if out else "kvleak: no match for {!r} in {} records".format(query, len(records))


# ---------------------------------------------------------------------- install


def hook_entries(command: str, max_chars: int) -> Dict[str, List[dict]]:
    """settings.json `hooks` entries for every kvleak hook; `command` runs kvleak."""
    def entry(event: str, matcher: Optional[str], timeout: int, extra: str = "") -> dict:
        e = {"hooks": [{"type": "command", "command": "{} hook {}{}".format(command, event, extra), "timeout": timeout}]}
        if matcher:
            e["matcher"] = matcher
        return e

    return {
        "PostToolUse": [entry("post-tool", "Bash", 15, " --max-chars {}".format(max_chars))],
        "PreCompact": [entry("pre-compact", None, 120)],
        "PostCompact": [entry("post-compact", None, 30)],
        "SessionStart": [entry("session-start", "compact", 30)],
    }


def is_kvleak_entry(entry: dict) -> bool:
    return any("kvleak" in (h.get("command") or "") and " hook " in (h.get("command") or "")
               for h in (entry.get("hooks") or []) if isinstance(h, dict))


def install(settings: dict, command: str, max_chars: int) -> dict:
    hooks = settings.setdefault("hooks", {})
    for event, entries in hook_entries(command, max_chars).items():
        kept = [e for e in (hooks.get(event) or []) if not is_kvleak_entry(e)]
        hooks[event] = kept + entries
    return settings


def uninstall(settings: dict) -> dict:
    hooks = settings.get("hooks") or {}
    for event in list(hooks):
        hooks[event] = [e for e in hooks[event] if not is_kvleak_entry(e)]
        if not hooks[event]:
            del hooks[event]
    if not hooks and "hooks" in settings:
        del settings["hooks"]
    return settings


def installed(settings: dict) -> List[str]:
    return [ev for ev, entries in (settings.get("hooks") or {}).items() if any(is_kvleak_entry(e) for e in entries)]


# ------------------------------------------------------------------- entry point


def run_hook(event: str, max_chars: int = DEFAULT_MAX_CHARS, stdin=None) -> int:
    """`kvleak hook <event>`: read the payload on stdin, print JSON output if any. Never fails loudly."""
    try:
        payload = json.load(stdin or sys.stdin)
    except ValueError:
        return 0
    try:
        if event == "post-tool":
            out = hook_post_tool(payload, max_chars)
        elif event == "pre-compact":
            out = hook_pre_compact(payload)
        elif event == "post-compact":
            out = hook_post_compact(payload)
        elif event == "session-start":
            out = hook_session_start(payload)
        else:
            out = None
    except Exception as exc:  # a hook must not break the session
        try:
            log = cache_root() / "hooks.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            with open(log, "a", encoding="utf-8") as f:
                f.write("{} {} {!r}\n".format(time.strftime("%Y-%m-%d %H:%M:%S"), event, exc))
        except OSError:
            pass
        return 0
    if out:
        sys.stdout.write(json.dumps(out))
        sys.stdout.flush()
    return 0
