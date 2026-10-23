"""Self-contained, shareable HTML report (no JS, no external assets)."""

from __future__ import annotations

from html import escape
from typing import List

from . import REPO_URL
from .analyze import CAUSES, Report, by_day, recommendations
from .fmt import duration, pct, tok, usd

COLORS = {
    "ttl_expiry": "#e5484d",
    "prefix_bust": "#8e4ec6",
    "tools_changed": "#b07cd8",
    "system_changed": "#c89ae6",
    "server_unavailable": "#8b8d98",
    "model_switch": "#f5a524",
    "compaction": "#3e63dd",
    "cold_start": "#12a594",
    "subagent_cold_start": "#7ce2d4",
}

CSS = """
:root{--bg:#fbfbfa;--fg:#1c1c1a;--muted:#6f6e69;--card:#fff;--line:#e6e5e0;--accent:#e5484d;--good:#30a46c}
@media (prefers-color-scheme:dark){:root{--bg:#141413;--fg:#ededec;--muted:#9b9a95;--card:#1d1d1b;--line:#2e2e2b}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 ui-sans-serif,system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:980px;margin:0 auto;padding:32px 16px 64px}h1{font-size:24px;margin:0 0 4px}h2{font-size:16px;margin:36px 0 12px}
.sub{color:var(--muted);margin:0 0 24px}.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}.kpi b{display:block;font-size:26px;font-variant-numeric:tabular-nums}
.kpi span{color:var(--muted);font-size:13px}.leak b{color:var(--accent)}.good b{color:var(--good)}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums;font-size:14px}th,td{padding:7px 8px;border-bottom:1px solid var(--line);text-align:left}
th{color:var(--muted);font-weight:500}td.n,th.n{text-align:right}.wrap{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:4px 8px}
.sw{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px;vertical-align:baseline}
.stack{display:flex;height:14px;border-radius:7px;overflow:hidden;background:var(--line)}code{font-size:13px}
ol li{margin:6px 0}footer{color:var(--muted);font-size:13px;margin-top:40px}svg text{fill:var(--muted);font-size:11px}
"""


def _m(r: Report, equiv: float, dollars: float) -> str:
    return usd(dollars) if r.unpriced_steps < r.steps else tok(equiv) + " tok"


def _daily_svg(r: Report) -> str:
    days = by_day(r.misses)
    if len(days) < 2:
        return ""
    w, h, pad = 940, 180, 24
    items = list(days.items())[-60:]
    peak = max(sum(v.values()) for _, v in items) or 1
    bw = (w - pad) / len(items)
    parts: List[str] = []
    for i, (day, vals) in enumerate(items):
        y = h - pad
        for cause in CAUSES:
            v = vals.get(cause, 0)
            if not v:
                continue
            bh = (h - 2 * pad) * v / peak
            y -= bh
            parts.append(
                '<rect x="{:.1f}" y="{:.1f}" width="{:.1f}" height="{:.1f}" fill="{}"><title>{} · {}: {} tok</title></rect>'.format(
                    pad + i * bw + 1, y, max(bw - 2, 1), bh, COLORS[cause], day, CAUSES[cause], tok(v)
                )
            )
        if i % max(len(items) // 8, 1) == 0:
            parts.append('<text x="{:.1f}" y="{}">{}</text>'.format(pad + i * bw, h - 6, day[5:]))
    return (
        '<h2>Leak per day</h2><div class="wrap"><svg viewBox="0 0 {} {}" width="100%" role="img" '
        'aria-label="Leaked tokens per day by cause">{}</svg></div>'.format(w, h, "".join(parts))
    )


def render_html(r: Report) -> str:
    stack = "".join(
        '<div title="{}" style="width:{:.2f}%;background:{}"></div>'.format(
            escape(CAUSES[c]), 100 * r.share(c), COLORS[c]
        )
        for c, b in r.buckets.items()
        if b["penalty"]
    )
    cause_rows = "".join(
        '<tr><td><span class="sw" style="background:{}"></span>{}</td><td class="n">{:,}</td>'
        '<td class="n">{}</td><td class="n">{}</td><td class="n">{}</td><td class="n">{}</td></tr>'.format(
            COLORS[c], escape(CAUSES[c]), int(b["count"]), tok(b["uncached"]), tok(b["rebuilt_median"]),
            _m(r, b["penalty"], b["penalty_usd"]), pct(r.share(c)),
        )
        for c, b in sorted(r.buckets.items(), key=lambda kv: -r.share(kv[0]))
        if b["count"]
    )
    sess_rows = "".join(
        "<tr><td><code>{}</code>{}</td><td>{}</td><td class='n'>{:,}</td><td class='n'>{}</td>"
        "<td class='n'>{}</td><td class='n'>{}</td></tr>".format(
            escape(s.session_id[:8]), " <small>(subagent)</small>" if s.is_subagent else "",
            escape(s.project), s.steps, pct(s.hit_rate), s.misses, _m(r, s.penalty, s.penalty_usd),
        )
        for s in r.worst_sessions
        if s.misses
    )
    miss_rows = "".join(
        "<tr><td>{}</td><td><code>{}</code></td><td><span class='sw' style='background:{}'></span>{}</td>"
        "<td class='n'>{}</td><td class='n'>{}</td><td class='n'>{}</td></tr>".format(
            escape((m.ts or "")[:16].replace("T", " ")), escape(m.session_id[:8]), COLORS[m.cause],
            escape(m.cause), duration(m.gap_s), tok(m.rebuilt),
            usd(m.penalty_usd) if m.penalty_usd is not None else tok(m.penalty) + " tok",
        )
        for m in sorted(r.misses, key=lambda m: -m.penalty)[:15]
    )
    tips = "".join("<li>{}</li>".format(escape(t)) for t in recommendations(r))
    saved = (r.no_cache_cost_usd - r.cost_usd) if r.unpriced_steps < r.steps else r.no_cache_cost - r.cost
    saved_s = usd(saved) if r.unpriced_steps < r.steps else tok(saved) + " tok"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>kvleak report</title><style>{CSS}</style></head><body><main>
<h1>Prompt-cache leak report</h1>
<p class="sub">{r.sessions - r.subagents} sessions + {r.subagents} subagents · {r.steps:,} model calls · generated by kvleak</p>
<div class="kpis">
<div class="kpi"><b>{pct(r.hit_rate)}</b><span>cache hit rate</span></div>
<div class="kpi"><b>{_m(r, r.cost, r.cost_usd)}</b><span>input spend (caching saved {saved_s})</span></div>
<div class="kpi leak"><b>{_m(r, r.leak, r.leak_usd)}</b><span>leaked to cache rebuilds ({pct(r.leak_fraction())} of input spend)</span></div>
<div class="kpi good"><b>{_m(r, r.keepalive_saving, r.keepalive_saving_usd)}</b><span>recoverable with capped keep-alive</span></div>
</div>
<h2>Where the leak comes from</h2>
<div class="stack">{stack}</div><br>
<div class="wrap"><table><tr><th>cause</th><th class="n">misses</th><th class="n">uncached</th><th class="n">per miss</th><th class="n">leak</th><th class="n">share</th></tr>{cause_rows}</table></div>
{_daily_svg(r)}
<h2>What to do about it</h2><ol>{tips}</ol>
<h2>Leakiest sessions</h2>
<div class="wrap"><table><tr><th>session</th><th>project</th><th class="n">calls</th><th class="n">hit rate</th><th class="n">misses</th><th class="n">leak</th></tr>{sess_rows}</table></div>
<h2>Most expensive single misses</h2>
<div class="wrap"><table><tr><th>when</th><th>session</th><th>cause</th><th class="n">idle gap</th><th class="n">rebuilt</th><th class="n">leak</th></tr>{miss_rows}</table></div>
<footer>“Leak” = what a miss cost on top of what the same context would have cost as a cache hit.
Token counts are base-equivalent input tokens. Generated by <a href="{REPO_URL}">kvleak</a>.</footer>
</main></body></html>
"""
