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
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

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


def projected_saving_30d(before: Snapshot, after: Snapshot) -> float:
    """What the after-side per-call input price, at the after-side call rate, is worth per 30 days."""
    if not after.calls or after.days <= 0:
        return 0.0
    per_call = before.input_per_call - after.input_per_call
    return per_call * after.calls * (30.0 / after.days)


def to_dict(before: Snapshot, after: Snapshot, at: float, events: Sequence[dict]) -> Dict:
    return {"applied_at": at, "events": list(events), "before": asdict(before), "after": asdict(after),
            "rows": [{"label": r.label, "before": r.before, "after": r.after, "delta": r.delta} for r in contrast(before, after)],
            "projected_saving_30d_usd": projected_saving_30d(before, after)}


PriceFor = Callable[[Optional[str]], Price]
