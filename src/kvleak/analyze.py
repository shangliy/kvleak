"""Classify every cache miss and put a price on it."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from typing import Dict, Iterable, List, Optional

from .pricing import TTL_1H, TTL_5M, Price, price_for
from .sources import Step, Transcript

# Order matters: this is also the display order.
CAUSES = {
    "ttl_expiry": "Idle gap outlived the cache TTL",
    "prefix_bust": "Prefix changed within the TTL",
    "model_switch": "Model switched mid-session",
    "compaction": "History compacted / rewritten",
    "cold_start": "New session cold start",
    "subagent_cold_start": "Subagent cold start",
}

KEEPALIVE_MARGIN_S = 30  # fire the keep-alive this long before expiry
# Only credit keep-alive for gaps a person would plausibly bridge (a meeting, lunch,
# a long build). Overnight gaps are break-even-positive on paper but nobody pings all night.
KEEPALIVE_MAX_IDLE_S = 2 * 3600
TTL_SLACK_S = 10  # transcript timestamps are written after generation ends


@dataclass
class Miss:
    session_id: str
    project: str
    path: str
    step_index: int
    ts: Optional[str]
    model: Optional[str]
    cause: str
    gap_s: Optional[float]
    uncached: int
    rebuilt: int  # part of `uncached` that was already-seen context
    penalty: float  # base-equivalent tokens paid on top of a cache hit
    penalty_usd: Optional[float]
    keepalive_pings: Optional[int] = None
    keepalive_saving: float = 0.0  # base-equivalent tokens a keep-alive would have saved


@dataclass
class SessionStats:
    session_id: str
    project: str
    path: str
    is_subagent: bool
    models: List[str]
    steps: int = 0
    uncached: int = 0
    read: int = 0
    output: int = 0
    cost: float = 0.0
    cost_usd: float = 0.0
    misses: int = 0
    penalty: float = 0.0
    penalty_usd: float = 0.0
    first_ts: Optional[str] = None
    last_ts: Optional[str] = None

    @property
    def hit_rate(self) -> float:
        total = self.uncached + self.read
        return self.read / total if total else 0.0


@dataclass
class Report:
    sessions: int = 0
    subagents: int = 0
    oneshot: int = 0  # short non-subagent transcripts, e.g. `claude -p` runs
    steps: int = 0
    input: int = 0
    write_5m: int = 0
    write_1h: int = 0
    read: int = 0
    output: int = 0
    cost: float = 0.0  # base-equivalent tokens actually billed for input
    cost_usd: float = 0.0
    output_usd: float = 0.0  # replies + thinking at the output price; input + output is the whole bill
    read_usd: float = 0.0  # the cache-read part of cost_usd
    write_usd: float = 0.0  # the cache-write part of cost_usd (uncached input is the rest)
    no_cache_cost: float = 0.0  # what input would have cost with no caching at all
    no_cache_cost_usd: float = 0.0
    unpriced_steps: int = 0
    model_mix: Dict[str, int] = field(default_factory=dict)
    sources: Dict[str, int] = field(default_factory=dict)  # which tool wrote the sessions: claude, codex, …
    buckets: Dict[str, Dict[str, float]] = field(default_factory=dict)
    keepalive_saving: float = 0.0
    keepalive_saving_usd: float = 0.0
    dead_write: float = 0.0  # write premium paid on tokens that were never read back
    dead_write_usd: float = 0.0
    dead_write_by_workload: Dict[str, float] = field(default_factory=dict)  # usd
    misses: List[Miss] = field(default_factory=list)
    worst_sessions: List[SessionStats] = field(default_factory=list)
    min_miss_tokens: int = 10_000

    @property
    def uncached(self) -> int:
        return self.input + self.write_5m + self.write_1h

    @property
    def hit_rate(self) -> float:
        total = self.uncached + self.read
        return self.read / total if total else 0.0

    @property
    def leak(self) -> float:
        return sum(b["penalty"] for b in self.buckets.values())

    @property
    def leak_usd(self) -> float:
        return sum(b["penalty_usd"] for b in self.buckets.values())

    @property
    def priced(self) -> bool:
        return self.unpriced_steps < self.steps

    def share(self, cause: str) -> float:
        """Fraction of the total leak caused by `cause` (dollar-weighted when prices are known)."""
        key, total = ("penalty_usd", self.leak_usd) if self.priced else ("penalty", self.leak)
        return self.buckets[cause][key] / total if total else 0.0

    def leak_fraction(self) -> float:
        if self.priced:
            return self.leak_usd / self.cost_usd if self.cost_usd else 0.0
        return self.leak / self.cost if self.cost else 0.0

    @property
    def miss_uncached(self) -> int:
        return int(sum(b["uncached"] for b in self.buckets.values()))

    def to_dict(self, top_misses: int = 50) -> dict:
        d = {
            k: v
            for k, v in asdict(self).items()
            if k not in ("misses", "worst_sessions")
        }
        d.update(
            uncached=self.uncached,
            hit_rate=round(self.hit_rate, 4),
            leak=round(self.leak),
            leak_usd=round(self.leak_usd, 2),
            worst_sessions=[{**asdict(s), "hit_rate": round(s.hit_rate, 4)} for s in self.worst_sessions],
            top_misses=[asdict(m) for m in sorted(self.misses, key=lambda m: -m.penalty)[:top_misses]],
        )
        return d


def step_cost(step: Step, price: Price) -> float:
    return (
        step.input
        + step.write_5m * price.write_5m_mult
        + step.write_1h * price.write_1h_mult
        + step.read * price.read_mult
    )


def _ttl_of(step: Step) -> int:
    return TTL_1H if step.write_1h > step.write_5m else TTL_5M


def classify(transcript: Transcript, min_miss_tokens: int = 10_000) -> List[Miss]:
    misses = []
    prev: Optional[Step] = None
    ttl = TTL_5M
    for i, step in enumerate(transcript.steps):
        if step.write_1h or step.write_5m:
            current_ttl = _ttl_of(step)
        else:
            current_ttl = ttl
        heavy = step.uncached >= min_miss_tokens and step.read < step.uncached
        if heavy:
            price = price_for(step.model)
            gap = (step.ts - prev.ts).total_seconds() if (prev and step.ts and prev.ts) else None
            if prev is None:
                cause = "subagent_cold_start" if transcript.is_subagent else "cold_start"
            elif step.after_compaction:
                cause = "compaction"
            elif prev.model and step.model and prev.model != step.model:
                cause = "model_switch"
            elif gap is not None and gap >= ttl - TTL_SLACK_S:
                cause = "ttl_expiry"
            else:
                cause = "prefix_bust"
            rebuilt = step.uncached if prev is None else min(step.uncached, prev.prompt)
            eff_write = step_cost(Step(None, None, step.input, step.write_5m, step.write_1h), price) / step.uncached
            penalty = max(rebuilt * (eff_write - price.read_mult), 0.0)
            miss = Miss(
                session_id=transcript.session_id,
                project=transcript.project,
                path=transcript.path,
                step_index=i,
                ts=step.ts.isoformat() if step.ts else None,
                model=step.model,
                cause=cause,
                gap_s=round(gap, 1) if gap is not None else None,
                uncached=step.uncached,
                rebuilt=rebuilt,
                penalty=penalty,
                penalty_usd=price.usd(penalty),
            )
            if cause == "ttl_expiry" and gap is not None:
                interval = max(ttl - KEEPALIVE_MARGIN_S, 60)
                # k pings keep the entry alive until k * interval + ttl after the last call.
                pings = max(1, math.ceil((gap - ttl) / interval))
                miss.keepalive_pings = pings
                if pings <= price.keepalive_kmax(ttl) and gap <= KEEPALIVE_MAX_IDLE_S:
                    miss.keepalive_saving = max(penalty - pings * rebuilt * price.read_mult, 0.0)
            misses.append(miss)
        prev = step
        ttl = current_ttl
    return misses


def analyze(
    transcripts: Iterable[Transcript],
    min_miss_tokens: int = 10_000,
    top_sessions: int = 10,
) -> Report:
    from .policy import dead_writes

    r = Report(min_miss_tokens=min_miss_tokens)
    r.buckets = {c: {"count": 0, "uncached": 0, "penalty": 0.0, "penalty_usd": 0.0} for c in CAUSES}
    models: Counter = Counter()
    sessions: List[SessionStats] = []
    transcripts = list(transcripts)
    dw = dead_writes(transcripts, price_for)
    r.dead_write, r.dead_write_usd, r.dead_write_by_workload = dw.premium, dw.premium_usd, dict(dw.by_workload)
    for t in transcripts:
        if not t.steps:
            continue
        r.sessions += 1
        if not t.is_subagent and len(t.steps) >= 5:
            r.sources[t.source] = r.sources.get(t.source, 0) + 1
        r.subagents += int(t.is_subagent)
        r.oneshot += int(not t.is_subagent and len(t.steps) < 5)
        s = SessionStats(t.session_id, t.project, t.path, t.is_subagent, [])
        seen_models: Dict[str, None] = {}
        for step in t.steps:
            price = price_for(step.model)
            cost = step_cost(step, price)
            r.steps += 1
            r.input += step.input
            r.write_5m += step.write_5m
            r.write_1h += step.write_1h
            r.read += step.read
            r.output += step.output
            r.cost += cost
            r.no_cache_cost += step.prompt
            if price.input_per_mtok is None:
                r.unpriced_steps += 1
            else:
                r.cost_usd += price.usd(cost)
                r.output_usd += price.usd(step.output * price.output_mult)
                r.read_usd += price.usd(step.read * price.read_mult)
                r.write_usd += price.usd(step.write_5m * price.write_5m_mult + step.write_1h * price.write_1h_mult)
                r.no_cache_cost_usd += price.usd(step.prompt)
                s.cost_usd += price.usd(cost)
            if step.model:
                models[step.model] += 1
                seen_models[step.model] = None
            s.steps += 1
            s.uncached += step.uncached
            s.read += step.read
            s.output += step.output
            s.cost += cost
        s.models = list(seen_models)
        s.first_ts = next((x.ts.isoformat() for x in t.steps if x.ts), None)
        s.last_ts = next((x.ts.isoformat() for x in reversed(t.steps) if x.ts), None)
        for m in classify(t, min_miss_tokens):
            b = r.buckets[m.cause]
            b["count"] += 1
            b["uncached"] += m.uncached
            b["penalty"] += m.penalty
            b["penalty_usd"] += m.penalty_usd or 0.0
            r.keepalive_saving += m.keepalive_saving
            p = price_for(m.model)
            r.keepalive_saving_usd += p.usd(m.keepalive_saving) or 0.0
            s.misses += 1
            s.penalty += m.penalty
            s.penalty_usd += m.penalty_usd or 0.0
            r.misses.append(m)
        sessions.append(s)
    r.model_mix = dict(models.most_common())
    sessions.sort(key=lambda s: -(s.penalty_usd if r.priced else s.penalty))
    r.worst_sessions = sessions[:top_sessions]
    return r


def recommendations(r: Report) -> List[str]:
    """Plain-English next steps, ordered by how much money they address."""
    tips = []
    b = r.buckets
    ranked = sorted(b.items(), key=lambda kv: -r.share(kv[0]))
    for cause, v in ranked:
        if v["count"] == 0 or r.share(cause) < 0.03:
            continue
        if cause == "ttl_expiry":
            extra = ""
            if r.keepalive_saving:
                extra = " About {:.0%} of this leak was recoverable with a keep-alive (`kvleak statusline` shows the countdown).".format(
                    r.keepalive_saving / v["penalty"] if v["penalty"] else 0
                )
            tips.append(
                "Idle gaps are your biggest leak class. Reply before the TTL ring empties, batch work into "
                "5-minute windows, or run long builds/tests in the background and come back in one go." + extra
            )
        elif cause == "prefix_bust":
            tips.append(
                "The prompt prefix is changing between calls. Look for timestamps, random IDs, unsorted JSON "
                "(`json.dumps(..., sort_keys=True)`), or tool lists that change order in system prompts, "
                "CLAUDE.md, hooks and MCP servers."
            )
        elif cause == "model_switch":
            tips.append(
                "Switching models throws away the whole cache (KV state is per model). Pin one model per "
                "session or per worker, and hand off between models with file pointers, not pasted context."
            )
        elif cause == "compaction":
            tips.append(
                "Compaction rewrites history and forces a full rebuild. Compact once at a natural break "
                "instead of letting auto-compact fire mid-task, and keep the session smaller in the first place."
            )
        elif cause == "subagent_cold_start":
            tips.append(
                "Many one-shot subagents each pay a cold-start tax. Reuse long-lived workers for routine retries, "
                "keep their system prompts identical so they share a cached prefix, and send cheap triage to "
                "scripts instead of agents."
            )
        elif cause == "cold_start":
            tips.append(
                "New sessions start cold. Resume (`claude --continue`) instead of starting over for the same task, "
                "and trim always-loaded context (MCP tool schemas, giant CLAUDE.md files)."
            )
    dead = r.dead_write_usd if r.priced else r.dead_write
    total = r.cost_usd if r.priced else r.cost
    if total and dead / total >= 0.03:
        wl = max(r.dead_write_by_workload, key=r.dead_write_by_workload.get, default="main")
        if wl == "headless":
            tips.append(
                "One-shot `claude -p` runs pay the 1h cache-write premium on prompts nothing reads back. "
                "Set CLAUDE_CODE_PROMPT_CACHE_TTL=5m for batch scripts, or move the batch to the Message Batches API."
            )
        else:
            tips.append(
                "A lot of cached content is written at the write premium and never read again (sessions that end "
                "right after a big tool result, or idle past the TTL). Finish with a short wrap-up turn only when "
                "you will continue, and let `kvleak tune` pick the TTL."
            )
    if not tips and r.steps:
        tips.append("No significant leaks found. Your cache discipline is already good.")
    return tips


def by_day(misses: List[Miss]) -> Dict[str, Dict[str, float]]:
    out: Dict[str, Dict[str, float]] = defaultdict(lambda: {c: 0.0 for c in CAUSES})
    for m in misses:
        if m.ts:
            out[m.ts[:10]][m.cause] += m.penalty
    return dict(sorted(out.items()))
