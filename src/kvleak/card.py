"""A shareable SVG card of the audit: the numbers people post, in one image.

Zero dependencies: the card is hand-written SVG. `to_png` converts it when a
rasteriser happens to be installed (rsvg-convert, magick/convert, inkscape);
otherwise the SVG is what you get, and a browser renders it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import List, Optional
from xml.sax.saxutils import escape

from .analyze import CAUSES, Report
from .fmt import pct, tok, usd
from .report import concentration_line

W, H = 900, 470
BG, FG, DIM, RED, GREEN, YEL, LINE = "#0f1117", "#e6e6e6", "#8b8f98", "#ff6b6b", "#4ade80", "#fbbf24", "#272a33"
FONT = "ui-monospace, SFMono-Regular, Menlo, Consolas, 'DejaVu Sans Mono', monospace"


def _t(x: int, y: int, text: str, size: int = 14, fill: str = FG, weight: str = "normal", anchor: str = "start") -> str:
    return '<text x="{}" y="{}" font-size="{}" fill="{}" font-weight="{}" text-anchor="{}">{}</text>'.format(
        x, y, size, fill, weight, anchor, escape(text))


def render_card(r: Report, label: str = "last 30 days") -> str:
    priced = r.priced
    money = (lambda e, d: usd(d)) if priced else (lambda e, d: tok(e) + " tok")
    out: List[str] = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" font-family="{f}">'.format(
            w=W, h=H, f=escape(FONT)),
        '<rect width="{}" height="{}" rx="14" fill="{}"/>'.format(W, H, BG),
    ]
    # header
    out.append(_t(36, 48, "kvleak", 22, FG, "bold"))
    out.append(_t(128, 48, "· prompt-cache leak report · " + label, 15, DIM))
    who = "{} sessions + {} subagents".format(f"{r.sessions - r.subagents - r.oneshot:,}", f"{r.subagents:,}")
    if r.oneshot:
        who += " + {} one-shot runs".format(f"{r.oneshot:,}")
    out.append(_t(36, 74, "{} · {} model calls".format(who, f"{r.steps:,}"), 13, DIM))
    out.append('<line x1="36" y1="92" x2="{}" y2="92" stroke="{}"/>'.format(W - 36, LINE))

    # three headline numbers
    hr = r.hit_rate
    cols = [
        (36, "Cache hit rate", pct(hr), GREEN if hr >= 0.9 else YEL if hr >= 0.75 else RED),
        (330, "Input spend", money(r.cost, r.cost_usd), FG),
        (624, "Leaked to misses", money(r.leak, r.leak_usd), RED),
    ]
    for x, name, value, color in cols:
        out.append(_t(x, 128, name, 13, DIM))
        out.append(_t(x, 168, value, 34, color, "bold"))
    out.append(_t(624, 192, "{} of input spend".format(pct(r.leak_fraction())), 13, DIM))
    saved = (r.no_cache_cost_usd - r.cost_usd) if priced else (r.no_cache_cost - r.cost)
    out.append(_t(330, 192, "caching saved {}".format(money(saved, saved)), 13, DIM))

    # top causes
    out.append(_t(36, 236, "Where the leak comes from", 14, FG, "bold"))
    causes = [k for k in sorted(CAUSES, key=lambda k: -r.share(k)) if r.buckets[k]["count"]][:3]
    y = 262
    bar_x, bar_w = 300, 330
    for cause in causes:
        share = r.share(cause)
        b = r.buckets[cause]
        out.append(_t(36, y + 4, CAUSES[cause], 13, FG))
        out.append('<rect x="{}" y="{}" width="{}" height="12" rx="3" fill="{}"/>'.format(bar_x, y - 6, bar_w, LINE))
        out.append('<rect x="{}" y="{}" width="{}" height="12" rx="3" fill="{}"/>'.format(
            bar_x, y - 6, max(2, int(bar_w * share)), RED if cause == "ttl_expiry" else YEL))
        out.append(_t(bar_x + bar_w + 14, y + 4, pct(share).rjust(6), 13, FG, anchor="start"))
        out.append(_t(W - 36, y + 4, money(b["penalty"], b["penalty_usd"]), 13, DIM, anchor="end"))
        y += 30
    if not causes:
        out.append(_t(36, y + 4, "no heavy misses", 13, DIM))

    line = concentration_line(r)
    if line:
        head, _, rest = line.partition(" · ")
        out.append(_t(36, 372, head + " ·", 12, DIM))
        out.append(_t(36, 390, rest, 12, DIM))

    out.append('<line x1="36" y1="412" x2="{}" y2="412" stroke="{}"/>'.format(W - 36, LINE))
    out.append(_t(36, 440, "uvx kvleak", 14, FG, "bold"))
    out.append(_t(140, 440, "· github.com/shangliy/kvleak", 12, DIM))
    out.append(_t(W - 36, 440, "local files only · list price · percentages are rate-limit headroom", 11, DIM, anchor="end"))
    out.append("</svg>")
    return "\n".join(out)


def _chrome(binary: str):
    return lambda src, dst: [
        binary, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=2",
        "--window-size={},{}".format(W, H), "--screenshot=" + dst, "file://" + os.path.abspath(src),
    ]


CONVERTERS = (
    ("rsvg-convert", lambda src, dst: ["rsvg-convert", "-z", "2", "-o", dst, src]),
    ("magick", lambda src, dst: ["magick", "-density", "144", src, dst]),
    ("convert", lambda src, dst: ["convert", "-density", "144", src, dst]),
    ("inkscape", lambda src, dst: ["inkscape", src, "--export-type=png", "--export-dpi=144", "--export-filename=" + dst]),
    ("google-chrome", _chrome("google-chrome")),
    ("google-chrome-stable", _chrome("google-chrome-stable")),
    ("chromium", _chrome("chromium")),
    ("chromium-browser", _chrome("chromium-browser")),
)


def to_png(svg_path: str, png_path: str) -> Optional[str]:
    """Rasterise with whatever is installed; returns the converter used, or None."""
    for name, argv in CONVERTERS:
        if shutil.which(name):
            try:
                subprocess.run(argv(svg_path, png_path), check=True, capture_output=True, timeout=60)
            except (subprocess.SubprocessError, OSError):
                continue
            if os.path.exists(png_path):
                return name
    return None
