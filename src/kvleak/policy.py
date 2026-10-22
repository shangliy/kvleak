"""Backtest the knobs Claude Code actually exposes, on your own history.

Claude Code lets you set the auto-compact window, the prompt-cache TTL for the
main conversation and for subagents, and the idle-compaction threshold. Each of
these moves real money, but the right value depends on how *you* work: how long
your sessions run, how long your breaks are, how many one-shot ``claude -p``
runs you fire. This module replays your transcripts under each candidate
setting and reports what it would have cost.

Everything here is money only. A compaction also loses detail, which no
backtest can price, so the recommendations leave that trade-off to you.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Sequence

from .pricing import TTL_1H, TTL_5M, Price
from .sources import Step, Transcript

PriceFor = Callable[[Optional[str]], Price]

IDLE_COMPACT_AFTER_S = 54 * 60  # Claude Code compacts idle 1h-TTL sessions at ~54 min
IDLE_COMPACT_DEFAULT_MIN = 200_000
WINDOW_GRID = [100_000, 150_000, 200_000, 300_000, 400_000, 500_000, 600_000, 800_000]
TTL_SLACK_S = 10


def workload(t: Transcript) -> str:
    """'main' (interactive), 'subagent', or 'headless' (short one-shot runs such as `claude -p`)."""
    if t.is_subagent:
        return "subagent"
    return "main" if len(t.steps) >= 5 else "headless"


def split(transcripts: Iterable[Transcript]) -> Dict[str, List[Transcript]]:
    out: Dict[str, List[Transcript]] = {"main": [], "subagent": [], "headless": []}
    for t in transcripts:
        if t.steps:
            out[workload(t)].append(t)
    return out


def _ttl_of(step: Step, default: int) -> int:
    if step.write_1h or step.write_5m:
        return TTL_1H if step.write_1h > step.write_5m else TTL_5M
    return default


def observed_ttl(transcripts: Iterable[Transcript]) -> Optional[str]:
    """Which TTL the writes in these transcripts mostly used ('5m' / '1h'), or None."""
    w5 = w1 = 0
    for t in transcripts:
        for s in t.steps:
            w5 += s.write_5m
            w1 += s.write_1h
    if not (w5 or w1):
        return None
    return "1h" if w1 > w5 else "5m"


# ----------------------------------------------------------------------- TTL policy


@dataclass
class TtlResult:
    ttl: str
    cost: float  # base-equivalent tokens
    cost_usd: float
    calls: int = 0


def _all_steps_in_order(transcripts: Sequence[Transcript]) -> List[Step]:
    steps = [s for t in transcripts for s in t.steps if s.ts]
    steps.sort(key=lambda s: s.ts)
    return steps


def simulate_ttl(transcripts: Sequence[Transcript], ttl: int, price_for: PriceFor, kind: str = "main") -> TtlResult:
    """Input cost if every cache write in these transcripts had used `ttl`.

    Within a session, a gap shorter than the TTL is a hit (new tokens written at
    the TTL's write price, the rest read) and a longer gap rebuilds everything.
    Misses that were not caused by the TTL (prefix busts, model switches) are
    kept as they happened. One-shot headless runs are a special case: they share
    a prefix across runs, so the gap that matters is the gap between runs.
    """
    res = TtlResult("1h" if ttl == TTL_1H else "5m", 0.0, 0.0)
    if kind == "headless":
        prev = None
        for s in _all_steps_in_order(transcripts):
            p = price_for(s.model)
            w = p.write_mult(ttl)
            cost = s.uncached * w + s.read * p.read_mult
            if prev is not None and s.read and (s.ts - prev.ts).total_seconds() >= ttl - TTL_SLACK_S:
                cost += s.read * (w - p.read_mult)  # the shared prefix went cold, rewrite it
            _add(res, cost, p)
            prev = s
        return res
    for t in transcripts:
        prev: Optional[Step] = None
        actual_ttl = TTL_5M
        for s in t.steps:
            p = price_for(s.model)
            w = p.write_mult(ttl)
            gap = (s.ts - prev.ts).total_seconds() if (prev and s.ts and prev.ts) else None
            hit = s.read >= s.uncached
            if gap is None:
                cost = s.uncached * w + s.read * p.read_mult
            elif gap >= ttl - TTL_SLACK_S:
                cost = s.prompt * w
            elif hit or gap < actual_ttl - TTL_SLACK_S:
                cost = s.uncached * w + s.read * p.read_mult  # as it happened (hit, or a non-TTL miss)
            else:  # a TTL miss that this TTL would have survived
                prefix = min(prev.prompt + prev.output, s.prompt)
                cost = prefix * p.read_mult + (s.prompt - prefix) * w
            _add(res, cost, p)
            actual_ttl = _ttl_of(s, actual_ttl)
            prev = s
    return res


def _add(res: TtlResult, cost: float, p: Price) -> None:
    res.cost += cost
    res.cost_usd += p.usd(cost) or 0.0
    res.calls += 1


def best_ttl(transcripts: Sequence[Transcript], price_for: PriceFor, kind: str = "main") -> Dict[str, TtlResult]:
    return {r.ttl: r for r in (simulate_ttl(transcripts, ttl, price_for, kind) for ttl in (TTL_5M, TTL_1H))}


# ------------------------------------------------------------- auto-compact window


@dataclass
class WindowResult:
    window: Optional[int]  # None = the advisor decides
    compactions: int
    sessions: int
    cost_usd: float
    saved_usd: float
    saved_pct: float


def learned_floor(transcripts: Iterable[Transcript], default: int) -> int:
    """Context size right after a real compaction, learned from history (median)."""
    sizes = sorted(s.prompt for t in transcripts if not t.is_subagent for s in t.steps if s.after_compaction and s.prompt)
    return sizes[len(sizes) // 2] if len(sizes) >= 3 else default


def window_sweep(transcripts: Sequence[Transcript], price_for: PriceFor, profile, grid: Sequence[int] = WINDOW_GRID) -> List[WindowResult]:
    from .advisor import backtest

    biggest = max((s.prompt for t in transcripts for s in t.steps), default=0)
    out = []
    for w in grid:
        if w > biggest:
            break
        b = backtest(transcripts, profile, price_for, window=w)
        out.append(WindowResult(w, b["compactions_suggested"], b["sessions"], b["input_cost_usd"], b["saved_usd"], b["saved_pct"]))
    return out


def pick_window(results: Sequence[WindowResult], floor: int = 100_000, knee: float = 0.8) -> Optional[WindowResult]:
    """Largest window that still captures `knee` of the best saving: most of the money, fewest compactions."""
    cands = [r for r in results if r.window and r.window >= floor and r.saved_usd > 0]
    if not cands:
        return None
    best = max(r.saved_usd for r in cands)
    return max((r for r in cands if r.saved_usd >= knee * best), key=lambda r: r.window or 0)


# ---------------------------------------------------------------- idle compaction


@dataclass
class IdleResult:
    min_tokens: int
    idle_gaps: int = 0
    compactions: int = 0
    rebuild_avoided_usd: float = 0.0
    compaction_cost_usd: float = 0.0

    @property
    def net_usd(self) -> float:
        return self.rebuild_avoided_usd - self.compaction_cost_usd


def idle_compaction(transcripts: Sequence[Transcript], price_for: PriceFor, min_tokens: int, floor: int,
                    summary_tokens: int = 8_000) -> IdleResult:
    """What Claude Code's idle compaction (compact ~54 min into a 1h-TTL idle gap) would have done.

    Counts only the rebuild it avoids minus what the compaction itself costs. The
    cheaper calls that follow are a bonus already credited to the window sweep.
    """
    res = IdleResult(min_tokens)
    for t in transcripts:
        prev: Optional[Step] = None
        for s in t.steps:
            if prev and s.ts and prev.ts:
                gap = (s.ts - prev.ts).total_seconds()
                ttl = _ttl_of(prev, TTL_1H)
                ctx = prev.prompt + prev.output
                if ttl == TTL_1H and gap >= IDLE_COMPACT_AFTER_S:
                    res.idle_gaps += 1
                    if ctx >= min_tokens:
                        p = price_for(prev.model)
                        res.compactions += 1
                        compact = ctx * p.read_mult + summary_tokens * p.output_mult + min(floor, ctx) * p.write_1h_mult
                        res.compaction_cost_usd += p.usd(compact) or 0.0
                        if gap >= ttl - TTL_SLACK_S and s.read < s.uncached:
                            rebuilt = min(s.uncached, ctx)
                            res.rebuild_avoided_usd += p.usd(rebuilt * p.write_mult(_ttl_of(s, ttl))) or 0.0
            prev = s
    return res


# -------------------------------------------------------------------- dead writes


@dataclass
class DeadWrites:
    """Cache writes paid at the write premium and never read back before they expired."""

    premium: float = 0.0  # base-equivalent tokens paid above the plain input price, wasted
    premium_usd: float = 0.0
    tokens: int = 0
    by_workload: Dict[str, float] = field(default_factory=lambda: defaultdict(float))  # usd

    @property
    def dominant(self) -> Optional[str]:
        return max(self.by_workload, key=self.by_workload.get) if self.by_workload else None


def dead_writes(transcripts: Sequence[Transcript], price_for: PriceFor) -> DeadWrites:
    res = DeadWrites()
    groups = split(transcripts)
    for kind, ts in groups.items():
        if kind == "headless":
            steps = _all_steps_in_order(ts)
            for i, s in enumerate(steps):
                nxt = steps[i + 1] if i + 1 < len(steps) else None
                written = s.write_5m + s.write_1h
                if not written:
                    continue
                ttl = _ttl_of(s, TTL_5M)
                reused = 0
                if nxt is not None and (nxt.ts - s.ts).total_seconds() < ttl:
                    reused = min(max(nxt.read - s.read, 0), written)  # the prefix grew: our write was read
                _credit(res, kind, s, written - reused, price_for(s.model))
            continue
        for t in ts:
            for i, s in enumerate(t.steps):
                written = s.write_5m + s.write_1h
                if not written:
                    continue
                nxt = t.steps[i + 1] if i + 1 < len(t.steps) else None
                ttl = _ttl_of(s, TTL_5M)
                reused = (nxt is not None and nxt.ts and s.ts and (nxt.ts - s.ts).total_seconds() < ttl
                          and nxt.read >= 0.9 * s.prompt)
                if not reused:
                    _credit(res, kind, s, written, price_for(s.model))
    return res


def _credit(res: DeadWrites, kind: str, s: Step, dead: int, p: Price) -> None:
    if dead <= 0:
        return
    share = dead / (s.write_5m + s.write_1h)
    premium = share * (s.write_5m * (p.write_5m_mult - 1) + s.write_1h * (p.write_1h_mult - 1))
    res.tokens += int(dead)
    res.premium += premium
    usd = p.usd(premium) or 0.0
    res.premium_usd += usd
    res.by_workload[kind] += usd


# --------------------------------------------------------------- subagent starts


@dataclass
class SubagentStarts:
    count: int = 0
    tokens: int = 0
    cost_usd: float = 0.0
    cold: int = 0  # first calls that read nothing at all: no sibling had warmed the shared prefix yet
    staggerable: int = 0  # cold starts that followed a sibling's launch within LAUNCH_WINDOW_S
    base: int = 0  # size of that shared prefix (system prompt + tools + CLAUDE.md), learned
    stagger_saving_usd: float = 0.0  # if each staggerable cold start had read the prefix instead of writing it

    @property
    def avg(self) -> float:
        return self.tokens / self.count if self.count else 0.0


LAUNCH_WINDOW_S = 120  # subagent first calls closer than this were launched together


def launched_together(firsts: Sequence[Step], window_s: float = LAUNCH_WINDOW_S) -> set:
    """ids of the first calls that followed a sibling's within `window_s`: the followers
    of each launch cluster. The earliest call of a cluster is its leader and writes the
    shared prefix whatever happens; a subagent launched on its own has no leader to wait for."""
    followers = set()
    prev_ts = None
    for s in sorted((s for s in firsts if s.ts is not None), key=lambda s: s.ts):
        if prev_ts is not None and (s.ts - prev_ts).total_seconds() <= window_s:
            followers.add(id(s))
        prev_ts = s.ts
    return followers


def subagent_starts(transcripts: Sequence[Transcript], price_for: PriceFor) -> SubagentStarts:
    """Subagents launched in parallel all start cold: the first one has not finished
    writing the shared prefix when the others send theirs. Launching one first and the
    rest once it has replied turns those writes into reads. Only the followers of a
    launch cluster count towards the stagger saving; a lone cold start is unavoidable."""
    res = SubagentStarts()
    firsts = [t.steps[0] for t in transcripts if t.is_subagent and t.steps]
    if not firsts:
        return res
    sizes = sorted(s.uncached for s in firsts)
    res.base = sizes[len(sizes) // 4]  # the lean quartile ≈ prefix without the task prompt
    followers = launched_together(firsts)
    for s in firsts:
        p = price_for(s.model)
        res.count += 1
        res.tokens += s.uncached
        res.cost_usd += p.usd(s.input + s.write_5m * p.write_5m_mult + s.write_1h * p.write_1h_mult) or 0.0
        if s.read < 2_000:
            res.cold += 1
            if id(s) in followers:
                res.staggerable += 1
                ttl = _ttl_of(s, TTL_5M)
                res.stagger_saving_usd += p.usd(min(res.base, s.uncached) * (p.write_mult(ttl) - p.read_mult)) or 0.0
    return res
