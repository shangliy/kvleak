"""When does a big context cost more than compacting it?

Caching makes a long context cheap, but not free: every call re-reads the whole
prefix at the cache-read price, and every miss rebuilds all of it at the write
price. Compacting (or handing off to a fresh session with a short doc) costs
money once, then makes every later call cheaper. It pays off after

    R* = one_time_cost / ((C - S) * r_eff)          calls

where C is the current context, S the context after compaction, and
r_eff = read + miss_rate * (write - read) the expected per-token price of
carrying one token of context for one more call. If you expect to keep going
for more than R* calls, compacting saves money.

Expected remaining calls come from your own history (``Profile``), so the advice
adapts to how long your sessions actually run.
"""

from __future__ import annotations

import bisect
import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from .pricing import TTL_1H, TTL_5M, Price
from .sources import Step, Transcript

# Claude Code-style compaction: the summary is a few thousand tokens and the
# system prompt + tool schemas are usually already cached across sessions.
DEFAULT_SUMMARY = 8_000
DEFAULT_BASE = 25_000
DEFAULT_MISS_RATE = 0.02  # heavy misses per call in typical interactive use
MIN_CONTEXT = 100_000  # below this, compaction is not worth losing detail over
HORIZON = 300  # never assume more than this many calls are still to come


def profile_path() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "kvleak" / "profile.json"


@dataclass
class Profile:
    """How *you* work, learned from past sessions."""

    lengths: List[int]  # calls per interactive main session, sorted
    miss_rate: float = DEFAULT_MISS_RATE
    compact_floor: int = DEFAULT_BASE + DEFAULT_SUMMARY  # context size right after a compaction
    keepalive: Dict[str, int] = field(default_factory=dict)  # TTL label -> pings worth sending (0: let it expire)
    keepalive_hours: Dict[str, Dict[str, int]] = field(default_factory=dict)  # TTL label -> local hour bucket -> pings

    def ping_budget(self, ttl_label: str, hour: Optional[int] = None) -> int:
        """Pings worth sending for a pause starting at this local hour (falls back to the overall budget)."""
        from .keepalive import HOUR_WIDTH

        if hour is not None:
            hours = self.keepalive_hours.get(ttl_label) or {}
            key = str(hour // HOUR_WIDTH * HOUR_WIDTH)
            if key in hours:
                return hours[key]
        return self.keepalive.get(ttl_label, 0)

    def expected_remaining(self, n: int) -> float:
        """Median of (L - n) over past sessions longer than n, capped at HORIZON.

        The median (not the mean) keeps a few marathon sessions from inflating the
        estimate. With too little history, fall back to Lindy: remaining ≈ elapsed.
        """
        i = bisect.bisect_right(self.lengths, n)
        longer = self.lengths[i:]
        if len(longer) < 5:
            return float(min(max(n, 10), HORIZON))
        return float(min(longer[len(longer) // 2] - n, HORIZON))

    @classmethod
    def from_transcripts(cls, transcripts: Iterable[Transcript], misses: int = 0, calls: int = 0) -> "Profile":
        from .keepalive import learned
        from .policy import learned_floor
        from .pricing import price_for

        transcripts = list(transcripts)
        lengths = sorted(len(t.steps) for t in transcripts if not t.is_subagent and len(t.steps) >= 5)
        rate = misses / calls if calls else DEFAULT_MISS_RATE
        keepalive, hours = learned(transcripts, price_for)
        return cls(lengths, round(rate, 4), learned_floor(transcripts, DEFAULT_BASE + DEFAULT_SUMMARY), keepalive, hours)

    def save(self) -> None:
        p = profile_path()
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps({**asdict(self), "updated": int(time.time())}), encoding="utf-8")
        except OSError:
            pass

    @classmethod
    def load(cls) -> "Profile":
        try:
            raw = json.loads(profile_path().read_text(encoding="utf-8"))
            return cls(sorted(raw["lengths"]), raw.get("miss_rate", DEFAULT_MISS_RATE),
                       int(raw.get("compact_floor", DEFAULT_BASE + DEFAULT_SUMMARY)),
                       {k: int(v) for k, v in (raw.get("keepalive") or {}).items()},
                       {k: {h: int(v) for h, v in d.items()} for k, d in (raw.get("keepalive_hours") or {}).items()})
        except (OSError, ValueError, KeyError):
            return cls([])


@dataclass
class Advice:
    context: int
    calls_so_far: int
    growth_per_call: float
    expected_remaining: float
    ttl: int
    # compaction
    compact_to: int
    compact_cost: float  # base-equivalent tokens, one-time
    compact_breakeven: float  # calls
    compact_saving: float  # expected, base-equivalent tokens
    # hand-off to a fresh session with a short doc
    handoff_to: int
    handoff_cost: float
    handoff_breakeven: float
    handoff_saving: float
    per_call_now: float  # what carrying the context costs per call today
    min_context: int = MIN_CONTEXT
    cold: bool = False

    @property
    def best(self) -> Optional[str]:
        if self.context < self.min_context:
            return None
        options = [(self.compact_saving, "compact"), (self.handoff_saving, "handoff")]
        saving, name = max(options)
        return name if saving > 0 else None


def _carry(start: float, growth: float, calls: float, r_eff: float) -> float:
    """Cost of `calls` more calls whose context starts at `start` and grows linearly."""
    return calls * (start + growth * calls / 2) * r_eff


def advise(
    context: int,
    price: Price,
    *,
    ttl: int = TTL_5M,
    calls_so_far: int = 1,
    growth_per_call: float = 2_000,
    profile: Optional[Profile] = None,
    summary_tokens: int = DEFAULT_SUMMARY,
    base_tokens: int = DEFAULT_BASE,
    handoff_doc_tokens: int = 3_000,
    min_context: int = MIN_CONTEXT,
    cold: bool = False,
) -> Advice:
    """`cold`: the cache has expired. Whatever happens next re-reads the whole context at
    full price (compaction) or rewrites it at the write price (continuing), which makes
    compacting *more* attractive, not less."""
    profile = profile or Profile([])
    m = profile.miss_rate
    write = price.write_mult(ttl)
    r_eff = price.read_mult + m * (write - price.read_mult)
    remaining = profile.expected_remaining(calls_so_far)
    if profile.compact_floor != DEFAULT_BASE + DEFAULT_SUMMARY:  # learned from real compactions
        base_tokens = max(profile.compact_floor - summary_tokens, 0)
    read_now = 1.0 if cold else price.read_mult  # price of reading the context one more time

    # Compact: read the whole thing once, write a summary, then rebuild base (cached) + summary.
    s_compact = base_tokens + summary_tokens
    compact_cost = context * read_now + summary_tokens * price.output_mult + summary_tokens * write
    # Hand-off: write a short doc, start a fresh session (base usually cached, doc is new).
    s_handoff = base_tokens + handoff_doc_tokens
    handoff_cost = context * read_now + handoff_doc_tokens * price.output_mult + handoff_doc_tokens * write
    if cold:  # the base prefix is cold too and must be rewritten either way
        compact_cost += base_tokens * write
        handoff_cost += base_tokens * write

    keep = _carry(context, growth_per_call, remaining, r_eff)
    if cold:
        keep += context * (write - r_eff)  # the next call rebuilds everything first

    def plan(s: int, one_time: float):
        saved_per_call = max(context - s, 1) * r_eff
        return one_time / saved_per_call, keep - (one_time + _carry(s, growth_per_call, remaining, r_eff))

    c_be, c_save = plan(s_compact, compact_cost)
    h_be, h_save = plan(s_handoff, handoff_cost)
    return Advice(
        context=context,
        calls_so_far=calls_so_far,
        growth_per_call=growth_per_call,
        expected_remaining=remaining,
        ttl=ttl,
        compact_to=s_compact,
        compact_cost=compact_cost,
        compact_breakeven=c_be,
        compact_saving=c_save,
        handoff_to=s_handoff,
        handoff_cost=handoff_cost,
        handoff_breakeven=h_be,
        handoff_saving=h_save,
        per_call_now=context * r_eff,
        min_context=min_context,
        cold=cold,
    )


def advise_steps(
    steps: List[Step],
    price: Price,
    profile: Optional[Profile] = None,
    calls_so_far: Optional[int] = None,
    min_context: int = MIN_CONTEXT,
    now: Optional[float] = None,
) -> Optional[Advice]:
    """Advice for the session these steps came from. With `now`, detects an expired cache."""
    if not steps:
        return None
    last = steps[-1]
    context = last.prompt + last.output
    window = steps[-50:]
    growth = (window[-1].prompt - window[0].prompt) / max(len(window) - 1, 1)
    ttl = TTL_1H if last.write_1h > last.write_5m else TTL_5M
    cold = bool(now is not None and last.ts is not None and now - last.ts.timestamp() > ttl)
    return advise(
        context,
        price,
        ttl=ttl,
        calls_so_far=calls_so_far or len(steps),
        growth_per_call=max(growth, 0.0),
        profile=profile,
        min_context=min_context,
        cold=cold,
    )


def backtest(transcripts: Iterable[Transcript], profile: Profile, price_for, min_context: int = MIN_CONTEXT,
             window: Optional[int] = None) -> dict:
    """Replay history with the advisor in the loop, using what actually happened next.

    Online rule: at each call, if the advisor (which only knows the past) says
    compacting saves money, compact right there. Savings are measured against the
    calls that really followed: each later prompt shrinks by the tokens dropped,
    priced at what that call actually paid per token (hit or miss). The context
    keeps growing afterwards, so a session can be compacted more than once.

    With `window`, the rule is Claude Code's own auto-compact instead: compact
    whenever the context reaches that many tokens, whatever the advisor thinks.
    """
    sessions = triggered = 0
    actual = saved = actual_usd = saved_usd = 0.0
    floor = profile.compact_floor
    for t in transcripts:
        if t.is_subagent or len(t.steps) < 5:
            continue
        sessions += 1
        dropped = 0.0  # tokens removed from every later prompt so far
        for i, step in enumerate(t.steps):
            p = price_for(step.model)
            cost = step.input + step.write_5m * p.write_5m_mult + step.write_1h * p.write_1h_mult + step.read * p.read_mult
            actual += cost
            actual_usd += p.usd(cost) or 0.0
            if step.after_compaction:
                dropped = 0.0  # the real compaction already threw the history away
            if dropped and step.prompt:
                cut = min(dropped, max(step.prompt - floor, 0))
                gain = cut * cost / step.prompt
                saved += gain
                saved_usd += p.usd(gain) or 0.0
            recent = t.steps[max(0, i - 49): i + 1]
            growth = max((recent[-1].prompt - recent[0].prompt) / max(len(recent) - 1, 1), 0.0)
            ttl = TTL_1H if step.write_1h > step.write_5m else TTL_5M
            eff = step.prompt + step.output - dropped
            a = advise(int(eff), p, ttl=ttl, calls_so_far=i + 1, growth_per_call=growth, profile=profile,
                       min_context=min_context)
            fire = eff >= window if window else (a.best and a.compact_saving > 0.2 * a.compact_cost)
            if fire and eff > a.compact_to:
                triggered += 1
                dropped += a.context - a.compact_to
                saved -= a.compact_cost
                saved_usd -= p.usd(a.compact_cost) or 0.0
    return {
        "min_context": min_context,
        "window": window,
        "sessions": sessions,
        "compactions_suggested": triggered,
        "input_cost": actual,
        "input_cost_usd": actual_usd,
        "saved": saved,
        "saved_usd": saved_usd,
        "saved_pct": saved / actual if actual else 0.0,
    }
