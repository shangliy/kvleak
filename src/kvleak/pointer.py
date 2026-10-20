"""Pointer messages for multi-agent hand-offs.

Instead of pasting a 500-line build log onto a shared board (where every agent
re-reads it on every turn), write it to disk and pass a ~60-token pointer:

    {"path": "/tmp/build.log", "sha256": "9f2c…", "lines": 512, "bytes": 40211,
     "tail": ["FAILED //pkg:test", "..."], "status": "FAIL"}

Only the agent that actually needs the details opens the file.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import List, Optional


def make_pointer(path: str, tail: int = 3, status: Optional[str] = None, note: Optional[str] = None) -> dict:
    h = hashlib.sha256()
    lines = 0
    last: List[str] = []
    with open(path, "rb") as f:
        for raw in f:
            h.update(raw)
            lines += 1
            if tail:
                last.append(raw.decode("utf-8", errors="replace").rstrip()[:200])
                if len(last) > tail:
                    last.pop(0)
    ptr = {
        "path": os.path.abspath(path),
        "sha256": h.hexdigest()[:16],
        "lines": lines,
        "bytes": os.path.getsize(path),
    }
    if tail:
        ptr["tail"] = last
    if status:
        ptr["status"] = status
    if note:
        ptr["note"] = note
    return ptr


def dumps(ptr: dict) -> str:
    # sort_keys keeps the bytes identical across runs, so the pointer itself never busts a cache.
    return json.dumps(ptr, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
