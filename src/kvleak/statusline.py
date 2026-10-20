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
from typing import List, Optional, Tuple

from .fmt import c, tok, usd
from .pricing import TTL_1H, TTL_5M, price_for
from .sources import Step, read_claude

TAIL_BYTES = 512 * 1024
RING = "○◔◑◕●"


def _recent_steps(path: str) -> Tuple[List[Step], int]:
    """Steps from the tail of a (possibly huge) transcript, plus an estimate of total calls."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return [], 0
    if size <= TAIL_BYTES:
        steps = read_claude(path).steps
        return steps, len(steps)
    steps = read_claude(path, tail_bytes=TAIL_BYTES).steps
    # Extrapolate the call count from the tail's density.
    return steps, int(len(steps) * size / TAIL_BYTES)


def render(payload: dict, now: Optional[float] = None) -> str:
    path = payload.get("transcript_path")
    if not path:
        return c("○ cache -", "grey")
    steps, n_calls = _recent_steps(os.path.expanduser(path))
    step = steps[-1] if steps else None
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
        return "{} {} · next msg rebuilds {} ({}){}".format(
            c("○", "red"), c("cache cold", "red", "bold"), tok(cached), cost, _compact_hint(steps, n_calls, price)
        )
    frac = left / ttl
    glyph = RING[min(int(frac * (len(RING) - 1)) + 1, len(RING) - 1)]
    color = "green" if left > 120 else "yellow" if left > 45 else "red"
    clock = "{}:{:02d}".format(int(left) // 60, int(left) % 60)
    return "{} cache {} · {} ctx · rebuild {}{}".format(
        c(glyph, color), c(clock, color, "bold"), tok(cached), cost, _compact_hint(steps, n_calls, price)
    )


def _compact_hint(steps: List[Step], n_calls: int, price) -> str:
    """' · ⚠ /compact saves ~$12' when carrying this context costs more than shrinking it."""
    from .advisor import Profile, advise_steps

    if price.input_per_mtok is None:
        return ""
    a = advise_steps(steps, price, Profile.load(), calls_so_far=n_calls)
    if not a or not a.best:
        return ""
    saving = price.usd(max(a.compact_saving, a.handoff_saving)) or 0
    if saving < 1:
        return ""
    return " · " + c("⚠ /compact saves ~{}".format(usd(saving)), "yellow", "bold")


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
