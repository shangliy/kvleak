"""Claude Code statusline: a live cache-TTL countdown.

Claude Code pipes a JSON blob to the statusline command on every refresh. Since
v2.1.251 it includes ``prompt_cache`` (TTL, expiry time) and
``context_window.current_usage`` (the last call's token counts), so we can show
the countdown without touching the transcript. Older versions only give
``transcript_path``; then we read the tail of the transcript instead.

    ◕ cache 3:41 · 182k ctx · rebuild $0.89
    ○ cache cold · next msg rebuilds 182k ($0.89)
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Callable, List, Optional, Tuple

from .fmt import c, tok, usd
from .pricing import TTL_1H, TTL_5M, price_for
from .sources import Step, read_claude

TAIL_BYTES = 512 * 1024
RING = "○◔◑◕●"
HINT_MIN_CONTEXT = 100_000  # below this the advisor never fires, so skip the transcript


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
    now = time.time() if now is None else now
    path = payload.get("transcript_path")
    path = os.path.expanduser(path) if path else None
    usage = (payload.get("context_window") or {}).get("current_usage") or {}
    cache = payload.get("prompt_cache") or {}
    model = (payload.get("model") or {}).get("id")
    steps_cache: List[Tuple[List[Step], int]] = []

    def steps() -> Tuple[List[Step], int]:
        if not steps_cache:
            steps_cache.append(_recent_steps(path) if path else ([], 0))
        return steps_cache[0]

    if cache.get("ttl") and usage:
        # Claude Code tells us directly: no transcript parsing on the hot path.
        ttl = TTL_1H if cache["ttl"] == "1h" else TTL_5M
        cached = sum(int(usage.get(k) or 0) for k in (
            "input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens"))
        expires = cache.get("expires_at")
        left = (float(expires) - now) if (expires and cache.get("warm")) else 0.0
    else:
        if not path:
            return c("○ cache -", "grey")
        last = steps()[0][-1] if steps()[0] else None
        if last is None or last.ts is None:
            return c("○ cache warming up", "grey")
        ttl = TTL_1H if last.write_1h > last.write_5m else TTL_5M
        cached = last.prompt + last.output  # the entry covers what was sent plus what came back
        model = last.model or model
        left = ttl - (now - last.ts.timestamp())
    price = price_for(model)
    rebuild = cached * price.write_mult(ttl)
    cost = usd(price.usd(rebuild)) if price.input_per_mtok is not None else tok(rebuild) + " tok"
    hint = _compact_hint(steps, price, now, cold=left <= 0) if cached >= HINT_MIN_CONTEXT else ""
    if left <= 0:
        return "{} {} · next msg rebuilds {} ({}){}".format(
            c("○", "red"), c("cache cold", "red", "bold"), tok(cached), cost, hint
        )
    frac = min(left / ttl, 1.0)
    glyph = RING[min(int(frac * (len(RING) - 1)) + 1, len(RING) - 1)]
    color = "green" if left > 120 else "yellow" if left > 45 else "red"
    clock = "{}:{:02d}".format(int(left) // 60, int(left) % 60)
    return "{} cache {} · {} ctx · rebuild {}{}".format(
        c(glyph, color), c(clock, color, "bold"), tok(cached), cost, hint
    )


def _compact_hint(steps: Callable[[], Tuple[List[Step], int]], price, now: float, cold: bool = False) -> str:
    """' · ⚠ /compact saves ~$12' when carrying this context costs more than shrinking it.

    On a cold cache the next message rebuilds everything at the write price, so
    compacting *first* (one full-price read, then a small prefix) saves even more.
    """
    from .advisor import Profile, advise_steps

    if price.input_per_mtok is None:
        return ""
    recent, n_calls = steps()
    if not recent:
        return ""
    a = advise_steps(recent, price, Profile.load(), calls_so_far=n_calls, now=now if cold else None)
    if not a or not a.best:
        return ""
    saving = price.usd(max(a.compact_saving, a.handoff_saving)) or 0
    if saving < 1:
        return ""
    verb = "/compact first saves" if cold else "/compact saves"
    return " · " + c("⚠ {} ~{}".format(verb, usd(saving)), "yellow", "bold")


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
