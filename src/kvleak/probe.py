"""Did it help? The same numbers before and after the day kvleak's changes went in.

`kvleak tune --apply`, `kvleak hooks --install` and `kvleak refine --apply`
record when they ran. `kvleak probe` then splits your sessions at that moment
(a session belongs to the side its first call falls on) and contrasts what
the bill looks like per call on each side, so "did kvleak save me anything"
has a number, measured on your own usage rather than replayed.

It is a before/after contrast, not a controlled experiment: the after side is
whatever you worked on since. Per-call and share metrics take most of that
out; the projected 30-day figure assumes the after-side call rate continues.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .pricing import Price
from .sources import Transcript

MIN_SESSIONS = 3  # fewer than this on the after side and the contrast is noise


def applied_path() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "kvleak" / "applied.json"


def load_events() -> List[dict]:
    try:
        raw = json.loads(applied_path().read_text(encoding="utf-8"))
        return [e for e in raw.get("events") or [] if isinstance(e, dict) and "at" in e]
    except (OSError, ValueError, AttributeError):
        return []


def record(what: str, at: Optional[float] = None) -> None:
    """Remember that `what` (tune, hooks, refine) was applied now."""
    events = load_events()
    events.append({"what": what, "at": float(at if at is not None else time.time())})
    p = applied_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"events": events}), encoding="utf-8")
    except OSError:
        pass


def first_applied(events: Sequence[dict]) -> Optional[float]:
    return min((float(e["at"]) for e in events), default=None)


@dataclass
class Snapshot:
    sessions: int = 0
    calls: int = 0
    days: float = 0.0
    input_usd: float = 0.0
    bill_usd: float = 0.0
    leak_share: float = 0.0
    context_per_call: float = 0.0  # mean prompt tokens per call, interactive sessions
    compactions_per_session: float = 0.0
    carry_usd_per_call: float = 0.0  # diet's carried cost, interactive sessions
    bash_tokens_per_call: float = 0.0
    strata: Dict[str, List[float]] = field(default_factory=dict)  # "model|depth" → [calls, input usd]

    @property
    def input_per_call(self) -> float:
        return self.input_usd / self.calls if self.calls else 0.0

    @property
    def bill_per_call(self) -> float:
        return self.bill_usd / self.calls if self.calls else 0.0


def session_start(t: Transcript) -> Optional[float]:
    for s in t.steps:
        if s.ts is not None:
            return s.ts.timestamp()
    return None


def snapshot(transcripts: Sequence[Transcript], start: float, end: float, miss_rate: float = 0.02,
             min_miss: int = 10_000) -> Snapshot:
    """Metrics over the sessions whose first call falls in [start, end)."""
    from .analyze import analyze
    from .diet import aggregate
    from .policy import split

    ts = [t for t in transcripts if (lambda s: s is not None and start <= s < end)(session_start(t))]
    snap = Snapshot(days=max((end - start) / 86400, 0.0))
    # Interactive sessions only: one-shot `claude -p` runs and subagents would swing the per-call
    # averages with the mix of work, and they are not what the settings, hooks and rules act on.
    main = split(ts)["main"]
    if not main:
        return snap
    r = analyze(main, min_miss, 0)
    snap.sessions = len(main)
    snap.calls = r.steps
    snap.input_usd = r.cost_usd
    snap.bill_usd = r.cost_usd + r.output_usd
    snap.leak_share = r.leak_fraction()
    snap.strata = strata(main)
    main_calls = sum(len(t.steps) for t in main)
    if main_calls:
        snap.context_per_call = sum(s.prompt for t in main for s in t.steps) / main_calls
        snap.compactions_per_session = sum(s.after_compaction for t in main for s in t.steps) / len(main)
        agg = aggregate(main, miss_rate)
        if agg.calls:
            snap.carry_usd_per_call = agg.carry_usd / agg.calls
            snap.bash_tokens_per_call = agg.kinds.get("Bash", {}).get("tokens", 0.0) / agg.calls
    return snap


@dataclass
class Row:
    label: str
    before: float
    after: float
    kind: str  # usd, pct, tok, num

    @property
    def delta(self) -> Optional[float]:
        if not self.before:
            return None
        return (self.after - self.before) / self.before


ROWS = (
    ("input spend per call", "input_per_call", "usd"),
    ("whole bill per call", "bill_per_call", "usd"),
    ("leaked share of input", "leak_share", "pct"),
    ("context per call", "context_per_call", "tok"),
    ("carried cost per call", "carry_usd_per_call", "usd"),
    ("compactions per session", "compactions_per_session", "num"),
    ("Bash result tokens per call", "bash_tokens_per_call", "tok"),
)


def contrast(before: Snapshot, after: Snapshot) -> List[Row]:
    return [Row(label, float(getattr(before, attr)), float(getattr(after, attr)), kind) for label, attr, kind in ROWS]


DEPTH_BUCKETS = (10, 25, 50, 100)  # call depth within a session: 1–10, 11–25, 26–50, 51–100, over 100


def depth_bucket(call_index: int) -> int:
    for b in DEPTH_BUCKETS:
        if call_index <= b:
            return b
    return 10**6


def strata(transcripts: Sequence[Transcript]) -> Dict[str, List[float]]:
    """Calls and input dollars per (model, call-depth bucket).

    The 80th call of a session carries far more context than the 5th, so a
    week of short sessions after `tune --apply` would look like a saving even
    if nothing changed. Comparing like with like takes that out.
    """
    from .analyze import step_cost
    from .pricing import price_for

    out: Dict[str, List[float]] = {}
    for t in transcripts:
        for i, s in enumerate(t.steps, 1):
            p = price_for(s.model)
            key = "{}|{}".format(s.model or "?", depth_bucket(i))
            cell = out.setdefault(key, [0, 0.0])
            cell[0] += 1
            cell[1] += p.usd(step_cost(s, p)) or 0.0
    return out


def decompose(before: Dict[str, List[float]], after: Dict[str, List[float]]) -> Tuple[float, float]:
    """Split the change in input dollars per call into (policy, mix).

    Oaxaca–Blinder: with `w` the share of calls and `Y` the dollars per call in
    each stratum, `Ȳ₁ − Ȳ₀ = Σ w₀ (Y₁ − Y₀) + Σ (w₁ − w₀) Y₁`. The first sum is
    what the same kind of call now costs (the policy's effect, at the before-side
    mix); the second is the work itself shifting between kinds of call. Strata
    present on one side only count as mix.
    """
    n0 = sum(c for c, _ in before.values())
    n1 = sum(c for c, _ in after.values())
    if not n0 or not n1:
        return 0.0, 0.0
    policy = mix = 0.0
    for g in set(before) | set(after):
        c0, u0 = before.get(g, (0, 0.0))
        c1, u1 = after.get(g, (0, 0.0))
        w0, w1 = c0 / n0, c1 / n1
        if c0 and c1:
            y0, y1 = u0 / c0, u1 / c1
            policy += w0 * (y1 - y0)
            mix += (w1 - w0) * y1
        elif c1:
            mix += w1 * u1 / c1
        else:
            mix -= w0 * u0 / c0
    return policy, mix


def projected_saving_30d(before: Snapshot, after: Snapshot, per_call: Optional[float] = None) -> float:
    """What a per-call input saving, at the after-side call rate, is worth per 30 days.

    Default: the raw difference in input dollars per call. Pass the policy part of
    `decompose` (negated) to project only the change the settings can claim."""
    if not after.calls or after.days <= 0:
        return 0.0
    if per_call is None:
        per_call = before.input_per_call - after.input_per_call
    return per_call * after.calls * (30.0 / after.days)


def to_dict(before: Snapshot, after: Snapshot, at: float, events: Sequence[dict]) -> Dict:
    policy, mix = decompose(before.strata, after.strata)
    return {"applied_at": at, "events": list(events), "before": asdict(before), "after": asdict(after),
            "rows": [{"label": r.label, "before": r.before, "after": r.after, "delta": r.delta} for r in contrast(before, after)],
            "projected_saving_30d_usd": projected_saving_30d(before, after),
            "policy_effect_per_call_usd": policy, "mix_effect_per_call_usd": mix,
            "projected_saving_30d_adjusted_usd": projected_saving_30d(before, after, -policy)}


PriceFor = Callable[[Optional[str]], Price]
