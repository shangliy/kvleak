"""Claude Code statusline: a live cache-TTL countdown.

Claude Code pipes a JSON blob to the statusline command on every refresh; it
includes ``transcript_path``. We read the tail of that transcript, find the
last billed call, and show how long its cache entry has left to live and what
it will cost to rebuild if it dies.

    ◕ cache 3:41 · 182k ctx · rebuild $0.89
    ○ cache cold · next msg rebuilds 182k ($0.89)
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Optional

from .fmt import c, tok, usd
from .pricing import TTL_1H, TTL_5M, price_for
from .sources import Step, parse_ts, read_claude

TAIL_BYTES = 512 * 1024
RING = "○◔◑◕●"


def _last_step(path: str) -> Optional[Step]:
    """Parse only the tail of a (possibly huge) transcript."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return None
    if size <= TAIL_BYTES:
        steps = read_claude(path).steps
        return steps[-1] if steps else None
    with open(path, "rb") as f:
        f.seek(size - TAIL_BYTES)
        f.readline()  # drop partial line
        lines = f.read().decode("utf-8", errors="replace").splitlines()
    for line in reversed(lines):
        if '"usage"' not in line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        msg = obj.get("message") or {}
        u = msg.get("usage") or {}
        if obj.get("type") != "assistant" or not u or msg.get("model") == "<synthetic>":
            continue
        created = int(u.get("cache_creation_input_tokens") or 0)
        w1h = min(int((u.get("cache_creation") or {}).get("ephemeral_1h_input_tokens") or 0), created)
        return Step(
            ts=parse_ts(obj.get("timestamp")),
            model=msg.get("model"),
            input=int(u.get("input_tokens") or 0),
            write_5m=created - w1h,
            write_1h=w1h,
            read=int(u.get("cache_read_input_tokens") or 0),
            output=int(u.get("output_tokens") or 0),
        )
    return None


def render(payload: dict, now: Optional[float] = None) -> str:
    path = payload.get("transcript_path")
    if not path:
        return c("○ cache -", "grey")
    step = _last_step(os.path.expanduser(path))
    if step is None or step.ts is None:
        return c("○ cache warming up", "grey")
    now = time.time() if now is None else now
    ttl = TTL_1H if step.write_1h > step.write_5m else TTL_5M
    # The entry covers everything the last call sent plus what it generated.
    cached = step.prompt + step.output
    price = price_for(step.model or (payload.get("model") or {}).get("id"))
    rebuild = cached * (price.write_1h_mult if ttl == TTL_1H else price.write_5m_mult)
    cost = usd(price.usd(rebuild)) if price.input_per_mtok is not None else tok(rebuild) + " tok"
    left = ttl - (now - step.ts.timestamp())
    if left <= 0:
        return "{} {} · next msg rebuilds {} ({})".format(
            c("○", "red"), c("cache cold", "red", "bold"), tok(cached), cost
        )
    frac = left / ttl
    glyph = RING[min(int(frac * (len(RING) - 1)) + 1, len(RING) - 1)]
    color = "green" if left > 120 else "yellow" if left > 45 else "red"
    clock = "{}:{:02d}".format(int(left) // 60, int(left) % 60)
    return "{} cache {} · {} ctx · rebuild {}".format(
        c(glyph, color), c(clock, color, "bold"), tok(cached), cost
    )


def main() -> int:
    from . import fmt

    fmt.set_color(os.environ.get("NO_COLOR") is None)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        payload = {}
    try:
        print(render(payload))
    except Exception:  # never break the user's prompt
        print("○ cache ?")
    return 0
