"""Terminal and Markdown renderings of a Report."""

from __future__ import annotations

from typing import List

from .analyze import CAUSES, LONG_SESSION_STEPS, Report, recommendations
from .fmt import bar, c, duration, pct, table, tok, usd

SOURCE_LABEL = {"claude": "Claude Code", "codex": "Codex CLI", "gemini": "Gemini CLI", "opencode": "OpenCode", "generic": "other"}

CAUSE_COLOR = {
    "ttl_expiry": "red",
    "prefix_bust": "magenta",
    "model_switch": "yellow",
    "compaction": "blue",
    "cold_start": "cyan",
    "subagent_cold_start": "cyan",
}


def _money_or_tokens(equiv: float, dollars: float, priced: bool) -> str:
    return usd(dollars) if priced else tok(equiv) + " tok"


def concentration_line(r: Report) -> str:
    """One sentence: is the leak a few sessions or spread across everything?"""
    k = r.concentration
    if not k or k["leaking_sessions"] < 2:
        return ""
    n_top = min(5, int(k["leaking_sessions"]))
    parts = ["top {} of {:,} leaking sessions carry {} of the leak".format(n_top, int(k["leaking_sessions"]), pct(k["top5_share"]))]
    if k["long_sessions"]:
        parts.append("{} session{} over {} calls carr{} {}".format(
            int(k["long_sessions"]), "s" if k["long_sessions"] != 1 else "", LONG_SESSION_STEPS,
            "y" if k["long_sessions"] != 1 else "ies", pct(k["long_share"])))
    verdict = "concentrated" if k["top5_share"] >= 0.4 else "spread out"
    return "Concentration: {} · {}".format(verdict, " · ".join(parts))


def render_terminal(r: Report, top_misses: int = 5) -> str:
    if r.steps == 0:
        return "No model calls with token usage found. Try `kvleak --path <dir-or-file>`."
    priced = r.unpriced_steps < r.steps
    out: List[str] = []
    title = "kvleak · prompt-cache leak report"
    out.append("")
    out.append(c(" " + title, "bold"))
    who = " {} sessions + {} subagents".format(r.sessions - r.subagents - r.oneshot, r.subagents)
    if r.oneshot:
        who += " + {} one-shot runs".format(f"{r.oneshot:,}")
    if len(r.sources) > 1:
        who += " ({})".format(", ".join("{} {}".format(SOURCE_LABEL.get(s, s), n) for s, n in sorted(r.sources.items(), key=lambda kv: -kv[1])))
    out.append(c("{} · {} model calls · {} min miss size".format(who, f"{r.steps:,}", tok(r.min_miss_tokens)), "grey"))
    out.append("")

    hr = r.hit_rate
    hr_color = "green" if hr >= 0.9 else "yellow" if hr >= 0.75 else "red"
    saved = r.no_cache_cost_usd - r.cost_usd if priced else r.no_cache_cost - r.cost
    leak_share = r.leak_fraction()
    out.append(
        "  Cache hit rate   {}  {}".format(c(pct(hr).rjust(6), "bold", hr_color), c(bar(hr, 24), hr_color))
    )
    out.append(
        "  Input spend      {}   {}".format(
            c(_money_or_tokens(r.cost, r.cost_usd, priced).rjust(8), "bold"),
            c("caching already saved you {}".format(usd(saved) if priced else tok(saved) + " tok"), "grey"),
        )
    )
    if priced and r.output_usd:
        bill = r.cost_usd + r.output_usd
        out.append(
            "  Output spend     {}   {}".format(
                c(usd(r.output_usd).rjust(8), "bold"),
                c("replies + thinking at the output price · whole bill {}: {} cache writes, {} cache reads, {} output".format(
                    usd(bill), pct(r.write_usd / bill), pct(r.read_usd / bill), pct(r.output_usd / bill)), "grey"),
            )
        )
    out.append(
        "  Leaked to misses {}   {}".format(
            c(_money_or_tokens(r.leak, r.leak_usd, priced).rjust(8), "bold", "red"),
            c("{} of input spend went to cache rebuilds and cold starts".format(pct(leak_share))
              + (" · {} of the whole bill".format(pct(r.leak_usd / (r.cost_usd + r.output_usd)))
                 if priced and r.output_usd else ""), "grey"),
        )
    )
    if r.keepalive_saving:
        out.append(
            "  Recoverable      {}   {}".format(
                c(_money_or_tokens(r.keepalive_saving, r.keepalive_saving_usd, priced).rjust(8), "bold", "green"),
                c("with keep-alive pings (idle ≤ 2h, capped at break-even; `kvleak keepalive` sizes the budget from when you come back)", "grey"),
            )
        )
    if r.dead_write:
        wl = max(r.dead_write_by_workload, key=r.dead_write_by_workload.get, default="")
        label = {"headless": "one-shot `claude -p` runs", "subagent": "subagents", "main": "interactive sessions"}.get(wl, "")
        out.append(
            "  Dead writes      {}   {}".format(
                c(_money_or_tokens(r.dead_write, r.dead_write_usd, priced).rjust(8), "bold", "yellow"),
                c("write premium on tokens never read back" + (" (mostly {})".format(label) if label else ""), "grey"),
            )
        )
    out.append("")

    out.append(c(" Where the leak comes from", "bold"))
    rows = [["cause", "misses", "uncached", "per miss", "leak", "share", ""]]
    for cause in sorted(CAUSES, key=lambda k: -r.share(k)):
        label, b = CAUSES[cause], r.buckets[cause]
        if not b["count"]:
            continue
        share = r.share(cause)
        rows.append(
            [
                c(label, CAUSE_COLOR[cause]),
                f"{int(b['count']):,}",
                tok(b["uncached"]),
                tok(b["rebuilt_median"]),
                _money_or_tokens(b["penalty"], b["penalty_usd"], priced),
                pct(share),
                c(bar(share, 16), CAUSE_COLOR[cause]),
            ]
        )
    out.append(table(rows, "lrrrrrl") if len(rows) > 1 else "  (no heavy misses)")
    line = concentration_line(r)
    if line:
        out.append(c("  " + line, "grey"))
    out.append("")

    if r.worst_sessions and r.worst_sessions[0].misses:
        out.append(c(" Leakiest sessions", "bold"))
        rows = [["session", "project", "calls", "hit rate", "misses", "leak", "models"]]
        for s in r.worst_sessions:
            if not s.misses:
                continue
            rows.append(
                [
                    s.session_id[:8] + (c(" (sub)", "grey") if s.is_subagent else ""),
                    s.project[:28],
                    f"{s.steps:,}",
                    pct(s.hit_rate),
                    str(s.misses),
                    _money_or_tokens(s.penalty, s.penalty_usd, priced),
                    ",".join(m.replace("claude-", "") for m in s.models)[:24],
                ]
            )
        out.append(table(rows, "llrrrrl"))
        out.append("")

    if top_misses and r.misses:
        out.append(c(" Most expensive single misses", "bold"))
        rows = [["when", "session", "cause", "idle gap", "rebuilt", "leak"]]
        for m in sorted(r.misses, key=lambda m: -m.penalty)[:top_misses]:
            rows.append(
                [
                    (m.ts or "")[:16].replace("T", " "),
                    m.session_id[:8],
                    c(m.cause, CAUSE_COLOR[m.cause]),
                    duration(m.gap_s),
                    tok(m.rebuilt),
                    _money_or_tokens(m.penalty, m.penalty_usd or 0, m.penalty_usd is not None),
                ]
            )
        out.append(table(rows, "lllrrr"))
        out.append("")

    out.append(c(" What to do about it", "bold"))
    for i, tip in enumerate(recommendations(r), 1):
        out.append("  {}. {}".format(i, tip))
    out.append("")
    if r.unpriced_steps:
        out.append(
            c(
                "  {} calls used models without a known price; they are counted in tokens only. "
                "Add prices in ~/.config/kvleak/pricing.json.".format(r.unpriced_steps),
                "grey",
            )
        )
    out.append(c("  Tune Claude Code's settings from this data: kvleak tune   ·   One session: kvleak session <id>", "grey"))
    out.append(c("  Share a redacted 4-line summary: kvleak --share", "grey"))
    return "\n".join(out)


def render_share(r: Report) -> str:
    """Redacted, copy-paste-ready 4-line summary for X / Reddit / GitHub Issue #1 (no paths or session IDs)."""
    if r.steps == 0:
        return "No model calls with token usage found."
    priced = r.unpriced_steps < r.steps
    m = lambda e, d: _money_or_tokens(e, d, priced)  # noqa: E731
    leak_share = pct(r.leak_fraction())
    top_cause = max(CAUSES, key=lambda k: r.share(k)) if r.leak > 0 else None
    if top_cause and r.buckets[top_cause]["count"]:
        b = r.buckets[top_cause]
        cause_line = "• #1 leak cause: {} ({}, {} across {:,} misses)".format(
            CAUSES[top_cause], pct(r.share(top_cause)), m(b["penalty"], b["penalty_usd"]), int(b["count"])
        )
    else:
        cause_line = "• #1 leak cause: none (no heavy misses)"
    return "\n".join([
        "My prompt-cache audit (kvleak):",
        "• Cache hit rate: {} ({:,} calls across {:,} sessions)".format(pct(r.hit_rate), r.steps, r.sessions),
        "• Input spend leaked to cache rebuilds: {} ({} of {})".format(leak_share, m(r.leak, r.leak_usd), m(r.cost, r.cost_usd)),
        cause_line,
        "Audit yours in 3s (local, zero deps): `uvx kvleak --share` · github.com/shangliy/kvleak/issues/1",
    ])


def render_markdown(r: Report) -> str:
    priced = r.unpriced_steps < r.steps
    m = lambda e, d: _money_or_tokens(e, d, priced)  # noqa: E731
    lines = [
        "## kvleak report",
        "",
        "| metric | value |",
        "|---|---|",
        f"| Sessions | {r.sessions - r.subagents - r.oneshot} (+{r.subagents} subagents, {r.oneshot} one-shot runs) |",
        f"| Model calls | {r.steps:,} |",
        f"| Cache hit rate | **{pct(r.hit_rate)}** |",
        f"| Input spend | {m(r.cost, r.cost_usd)} |",
        f"| Leaked to cache misses | **{m(r.leak, r.leak_usd)}** |",
        f"| Recoverable with keep-alive | {m(r.keepalive_saving, r.keepalive_saving_usd)} |",
        f"| Dead cache writes | {m(r.dead_write, r.dead_write_usd)} |",
        "",
        "| cause | misses | uncached tokens | leak |",
        "|---|---:|---:|---:|",
    ]
    for cause, label in CAUSES.items():
        b = r.buckets[cause]
        if b["count"]:
            lines.append(f"| {label} | {int(b['count']):,} | {tok(b['uncached'])} | {m(b['penalty'], b['penalty_usd'])} |")
    lines += ["", "**Next steps**", ""]
    lines += [f"{i}. {t}" for i, t in enumerate(recommendations(r), 1)]
    return "\n".join(lines) + "\n"
