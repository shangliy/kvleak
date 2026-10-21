"""Learned keep-alive budget: how many refresh pings your own idle pattern justifies.

A keep-alive ping re-reads the cached prefix (``read`` × N) shortly before it
expires. It pays only if you come back while the entry is still warm, because
then the next turn skips the ``(write − read) × N`` rebuild. Ping into an
overnight gap and every ping is wasted. The break-even count
(``Price.keepalive_kmax``) says when pinging *can* pay; it says nothing about
whether *you* come back in time. Your transcripts do.

For every pause that outlived the TTL (including the final pause of a session,
which never came back) this module asks: under a policy of "ping up to k
times, then give up", what would it have cost or saved? The k with the best
total is the budget to give a keep-alive tool such as cache-refresher's
``/cache-auto on <k>``.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from .analyze import KEEPALIVE_MARGIN_S, TTL_SLACK_S
from .pricing import TTL_1H, TTL_5M, Price
from .sources import Transcript

# A session whose last call is newer than this may still be going: not a "never came back".
OPEN_SESSION_S = 2 * 3600
MIN_PREFIX = 10_000  # pauses on tiny prefixes are not worth a decision either way
KNEE_SHARE = 0.8  # recommend the smallest ping count that gets this share of the optimum's saving


@dataclass
class Pause:
    """A moment the conversation went quiet for longer than the cache lived."""

    ts: float
    gap_s: Optional[float]  # None: the session ended here
    prefix: int  # tokens the cache entry held
    ttl: int
    model: Optional[str]

    def pings_needed(self) -> Optional[int]:
        """Pings that would have bridged the gap, or None if it never closed."""
        if self.gap_s is None:
            return None
        interval = max(self.ttl - KEEPALIVE_MARGIN_S, 60)
        return max(1, math.ceil((self.gap_s - self.ttl) / interval))


@dataclass
class PolicyRow:
    pings: int
    returned: float  # share of pauses (prefix-weighted) that came back within `pings`
    saving_usd: float  # vs. never pinging; negative means pinging lost money
    saving: float  # base-equivalent tokens


@dataclass
class Budget:
    ttl: int
    pauses: int
    never_returned: int
    kmax: int
    rows: List[PolicyRow] = field(default_factory=list)
    model: Optional[str] = None

    @property
    def best(self) -> PolicyRow:
        return max(self.rows, key=lambda r: (r.saving_usd, -r.pings))

    def knee(self, share: float = KNEE_SHARE) -> int:
        """Smallest ping count that already captures `share` of the best saving."""
        best = self.best.saving_usd
        if best <= 0:
            return 0
        return next(r.pings for r in self.rows if r.saving_usd >= share * best)

    @property
    def recommended(self) -> int:
        """Pings worth sending; 0 means let it expire.

        The knee, not the optimum: past it the curve is flat, so the optimum
        wanders with a handful of overnight returns and costs several times
        the requests (quota on a subscription) for the last fifth of the saving.
        """
        return self.knee()


def pauses(transcripts: Iterable[Transcript], now: Optional[float] = None, min_prefix: int = MIN_PREFIX) -> List[Pause]:
    now = time.time() if now is None else now
    out: List[Pause] = []
    for t in transcripts:
        if t.is_subagent or len(t.steps) < 2:
            continue
        ttl = TTL_5M
        steps = [s for s in t.steps if s.ts is not None]
        for i, step in enumerate(steps):
            if step.write_1h or step.write_5m:
                ttl = TTL_1H if step.write_1h > step.write_5m else TTL_5M
            prefix = step.prompt + step.output
            if prefix < min_prefix:
                continue
            at = step.ts.timestamp()
            if i + 1 < len(steps):
                gap = steps[i + 1].ts.timestamp() - at
                if gap < ttl - TTL_SLACK_S:
                    continue  # came back in time: no decision to make
                out.append(Pause(at, gap, prefix, ttl, step.model))
            elif now - at >= OPEN_SESSION_S:
                out.append(Pause(at, None, prefix, ttl, step.model))
    return out


def budget(pauses_: List[Pause], price_for: Callable[[Optional[str]], Price], ttl: int) -> Optional[Budget]:
    """Total outcome of "ping up to k times" over every pause with this TTL."""
    ps = [p for p in pauses_ if p.ttl == ttl]
    if not ps:
        return None
    kmax = max(price_for(p.model).keepalive_kmax(ttl) for p in ps)
    weight = sum(p.prefix for p in ps)
    b = Budget(ttl, len(ps), sum(1 for p in ps if p.gap_s is None), kmax)
    models: Dict[str, int] = {}
    for p in ps:
        models[p.model or "?"] = models.get(p.model or "?", 0) + 1
    b.model = max(models, key=models.get)
    for k in range(0, kmax + 1):
        saving = usd = 0.0
        returned = 0
        for p in ps:
            price = price_for(p.model)
            j = p.pings_needed()
            if j is not None and j <= k:
                delta = p.prefix * (price.write_mult(ttl) - price.read_mult) - j * p.prefix * price.read_mult
                returned += p.prefix
            else:
                delta = -k * p.prefix * price.read_mult
            saving += delta
            usd += price.usd(delta) or 0.0
        b.rows.append(PolicyRow(k, returned / weight if weight else 0.0, usd, saving))
    return b


def budgets(transcripts: Iterable[Transcript], price_for: Callable[[Optional[str]], Price],
            now: Optional[float] = None) -> List[Budget]:
    ps = pauses(transcripts, now)
    out = [b for b in (budget(ps, price_for, ttl) for ttl in (TTL_1H, TTL_5M)) if b]
    return sorted(out, key=lambda b: -b.pauses)


HOUR_WIDTH = 3  # local-time buckets: 00–02, 03–05, …
HOUR_MIN_PAUSES = 8  # fewer than this in a bucket and the overall budget is used instead


def hour_bucket(ts: float, width: int = HOUR_WIDTH) -> int:
    return datetime.fromtimestamp(ts).hour // width * width


def by_hour(pauses_: List[Pause], price_for: Callable[[Optional[str]], Price], ttl: int,
            width: int = HOUR_WIDTH, min_pauses: int = HOUR_MIN_PAUSES) -> List[Tuple[int, Budget]]:
    """The same budget per local time of day the pause began: lunch and midnight differ."""
    groups: Dict[int, List[Pause]] = {}
    for p in pauses_:
        if p.ttl == ttl:
            groups.setdefault(hour_bucket(p.ts, width), []).append(p)
    out = []
    for start in sorted(groups):
        if len(groups[start]) >= min_pauses:
            b = budget(groups[start], price_for, ttl)
            if b:
                out.append((start, b))
    return out


def ttl_label(ttl: int) -> str:
    return "1h" if ttl == TTL_1H else "5m"


def learned(transcripts: Iterable[Transcript], price_for: Callable[[Optional[str]], Price],
            now: Optional[float] = None) -> Tuple[Dict[str, int], Dict[str, Dict[str, int]]]:
    """(overall budget per TTL, budget per TTL per local hour bucket) for the profile."""
    ps = pauses(transcripts, now)
    overall: Dict[str, int] = {}
    hours: Dict[str, Dict[str, int]] = {}
    for ttl in (TTL_1H, TTL_5M):
        b = budget(ps, price_for, ttl)
        if b:
            overall[ttl_label(ttl)] = b.recommended
            hours[ttl_label(ttl)] = {str(h): hb.recommended for h, hb in by_hour(ps, price_for, ttl)}
    return overall, hours


def return_curve(b: Budget) -> List[PolicyRow]:
    """The rows worth showing: 0, every k up to the best, then a few past it."""
    best = b.best.pings
    keep = {0, best, best + 1, best + 2, b.kmax}
    keep.update(range(0, min(best, 5) + 1))
    return [r for r in b.rows if r.pings in keep]
