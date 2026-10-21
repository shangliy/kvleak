"""Readers for other coding agents' session stores: Codex CLI, Gemini CLI, OpenCode.

Each one turns the files a tool already writes into `Transcript`s of billed
`Step`s, so `kvleak audit`, `advise`, `keepalive` and `whatif` work on them
unchanged. Context-level features (`diet`, `tune`, `hooks`) read Claude Code
transcripts only, because they need the tool results themselves.

* Codex CLI: ``$CODEX_HOME/sessions/YYYY/MM/DD/rollout-*.jsonl`` (and
  ``archived_sessions/``). Usage comes from ``event_msg`` lines with
  ``payload.type == "token_count"``: ``last_token_usage`` per response, or the
  delta of ``total_token_usage`` when only that is present. OpenAI counts cached
  and cache-written tokens inside ``input_tokens``; they are split out here.
* Gemini CLI: ``~/.gemini/tmp/<project>/chats/session-*.json`` (one document,
  CLI ≤ 0.38) or ``session-*.jsonl`` (≥ 0.39). Messages with ``type: "gemini"``
  carry ``tokens: {input, cached, output, thoughts}``; ``input`` includes
  ``cached``; thoughts are billed as output.
* OpenCode: ``$XDG_DATA_HOME/opencode/opencode*.db`` (SQLite). 1.x keeps
  assistant messages in ``message.data`` (``role``, ``modelID``, ``tokens``);
  2.x in ``session_message`` (``type``, ``data.model.id``, ``data.tokens``).
  ``tokens`` is ``{input, output, reasoning, cache: {read, write}}``. Child
  sessions (``parent_id``) are subagents.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional

from .sources import Step, Transcript, _int, parse_ts

CODEX = "codex"
GEMINI = "gemini"
OPENCODE = "opencode"


def codex_root() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def gemini_root() -> Path:
    return Path(os.environ.get("GEMINI_CLI_HOME") or Path.home() / ".gemini") / "tmp"


def opencode_root() -> Path:
    override = os.environ.get("OPENCODE_DATA_DIR")
    if override:
        return Path(override)
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "opencode"


def default_roots() -> List[str]:
    """Every session store on this machine that kvleak knows how to read."""
    from .sources import default_claude_root

    roots = [default_claude_root(), codex_root() / "sessions", codex_root() / "archived_sessions",
             gemini_root(), opencode_root()]
    return [str(p) for p in roots if p.exists()]


# ---------------------------------------------------------------------------- codex


def is_codex(first_line: str) -> bool:
    return '"session_meta"' in first_line


def read_codex(path: str) -> Transcript:
    steps: List[Step] = []
    session_id = Path(path).stem
    cwd: Optional[str] = None
    model: Optional[str] = None
    prev_total: Optional[dict] = None
    prev_sum = -1
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if '"token_count"' not in line and '"session_meta"' not in line and '"turn_context"' not in line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            kind = obj.get("type")
            payload = obj.get("payload") or {}
            if kind == "session_meta":
                session_id = payload.get("id") or session_id
                cwd = payload.get("cwd") or cwd
                continue
            if kind == "turn_context":
                model = payload.get("model") or model
                continue
            if kind != "event_msg" or payload.get("type") != "token_count":
                continue
            info = payload.get("info")
            if not info:
                continue  # rate-limit ping, no usage
            total = info.get("total_token_usage") or {}
            tot = _int(total.get("total_tokens"))
            if total and tot == prev_sum:
                continue  # the same response reported twice
            last = info.get("last_token_usage")
            if not last and total and prev_total is not None:
                last = {k: _int(total.get(k)) - _int(prev_total.get(k)) for k in total}
            elif not last and total:
                last = total
            if total:
                prev_total, prev_sum = total, tot
            if not last:
                continue
            read = _int(last.get("cached_input_tokens"))
            write = _int(last.get("cache_write_input_tokens"))
            inp = max(_int(last.get("input_tokens")) - read - write, 0)
            if inp + read + write == 0:
                continue
            steps.append(Step(ts=parse_ts(obj.get("timestamp")), model=model, input=inp, write_5m=write,
                              read=read, output=_int(last.get("output_tokens"))))
    project = Path(cwd).name if cwd else Path(path).parent.name
    return Transcript(path, session_id, project or "?", False, steps, CODEX)


# --------------------------------------------------------------------------- gemini


def is_gemini_path(path: str) -> bool:
    p = path.replace(os.sep, "/")
    return "/chats/session-" in p and (p.endswith(".json") or p.endswith(".jsonl"))


def _gemini_step(msg: dict, fallback_ts) -> Optional[Step]:
    if msg.get("type") != "gemini" or not isinstance(msg.get("tokens"), dict):
        return None
    t = msg["tokens"]
    read = _int(t.get("cached"))
    inp = max(_int(t.get("input")) - read, 0)
    if inp + read == 0:
        return None
    return Step(ts=parse_ts(msg.get("timestamp") or fallback_ts), model=msg.get("model"), input=inp, read=read,
                output=_int(t.get("output")) + _int(t.get("thoughts")))


def read_gemini(path: str) -> Transcript:
    raw = open(path, "r", encoding="utf-8", errors="replace").read()
    steps: List[Step] = []
    session_id = Path(path).stem.replace("session-", "")
    start = None
    try:
        doc = json.loads(raw)
    except ValueError:
        doc = None
    if isinstance(doc, dict) and isinstance(doc.get("messages"), list):
        session_id = doc.get("sessionId") or session_id
        start = doc.get("startTime")
        for msg in doc["messages"]:
            s = _gemini_step(msg, start) if isinstance(msg, dict) else None
            if s:
                steps.append(s)
    else:
        for line in raw.splitlines():
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if not isinstance(obj, dict) or "$set" in obj:
                continue
            if obj.get("sessionId") and obj.get("startTime") and start is None:
                session_id, start = obj["sessionId"], obj["startTime"]
                continue
            s = _gemini_step(obj, start)
            if s:
                steps.append(s)
    project = Path(path).parent.parent.name  # the project hash; Gemini does not store the path
    return Transcript(path, session_id, project[:12], False, steps, GEMINI)


# ------------------------------------------------------------------------- opencode


def is_opencode_path(path: str) -> bool:
    name = os.path.basename(path)
    return name.startswith(os.environ.get("OPENCODE_DB_PREFIX") or "opencode") and name.endswith(".db")


def _tokens_step(data: dict, ts, model: Optional[str]) -> Optional[Step]:
    t = data.get("tokens")
    if not isinstance(t, dict):
        return None
    cache = t.get("cache") if isinstance(t.get("cache"), dict) else {}
    inp, read, write = _int(t.get("input")), _int(cache.get("read")), _int(cache.get("write"))
    if inp + read + write == 0:
        return None
    return Step(ts=parse_ts(ts), model=model, input=inp, write_5m=write, read=read,
                output=_int(t.get("output")) + _int(t.get("reasoning")))


def read_opencode(path: str) -> List[Transcript]:
    """One Transcript per OpenCode session in the database; child sessions are subagents."""
    try:
        con = sqlite3.connect("file:{}?mode=ro".format(path), uri=True)
    except sqlite3.Error:
        return []
    out: List[Transcript] = []
    try:
        tables = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
        sess_table = "session_v2" if "session_v2" in tables else "session" if "session" in tables else None
        meta: Dict[str, tuple] = {}
        if sess_table:
            cols = {r[1] for r in con.execute("pragma table_info({})".format(sess_table))}
            want = [c for c in ("id", "parent_id", "directory", "title") if c in cols]
            for row in con.execute("select {} from {}".format(", ".join(want), sess_table)):
                d = dict(zip(want, row))
                meta[d["id"]] = (d.get("parent_id"), d.get("directory") or d.get("title") or "")
        steps_by: Dict[str, List[Step]] = {}
        if "session_message" in tables:
            q = "select session_id, type, time_created, data from session_message order by time_created, session_id, seq"
            for sid, typ, ts, data in con.execute(q):
                if typ not in ("assistant", "model"):
                    continue
                d = _json(data)
                model = ((d.get("model") or {}).get("id") if isinstance(d.get("model"), dict) else None) or d.get("modelID")
                s = _tokens_step(d, ts, model)
                if s:
                    steps_by.setdefault(sid, []).append(s)
        elif "message" in tables:
            q = "select session_id, time_created, data from message order by time_created, id"
            for sid, ts, data in con.execute(q):
                d = _json(data)
                if d.get("role") not in ("assistant", "model"):
                    continue
                s = _tokens_step(d, ts, d.get("modelID"))
                if s:
                    steps_by.setdefault(sid, []).append(s)
        for sid, steps in steps_by.items():
            parent, where = meta.get(sid, (None, ""))
            project = Path(where).name if where else Path(path).parent.name
            out.append(Transcript("{}#{}".format(path, sid), sid, project or "?", parent is not None, steps, OPENCODE))
    except sqlite3.Error:
        pass
    finally:
        con.close()
    return out


def _json(blob) -> dict:
    if isinstance(blob, (bytes, bytearray)):
        blob = blob.decode("utf-8", errors="replace")
    try:
        d = json.loads(blob or "{}")
    except ValueError:
        return {}
    return d if isinstance(d, dict) else {}
