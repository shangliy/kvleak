"""Tiny formatting helpers (no third-party deps)."""

from __future__ import annotations

import os
import sys
from typing import List, Optional, Sequence

_COLOR = None


def color_enabled() -> bool:
    global _COLOR
    if _COLOR is None:
        _COLOR = (
            sys.stdout.isatty()
            and os.environ.get("NO_COLOR") is None
            and os.environ.get("TERM") != "dumb"
        ) or bool(os.environ.get("FORCE_COLOR"))
    return _COLOR


def set_color(on: bool) -> None:
    global _COLOR
    _COLOR = on


_CODES = {
    "bold": "1", "dim": "2", "red": "31", "green": "32", "yellow": "33",
    "blue": "34", "magenta": "35", "cyan": "36", "grey": "90",
}


def c(text: str, *styles: str) -> str:
    if not color_enabled() or not styles:
        return text
    return "\033[{}m{}\033[0m".format(";".join(_CODES[s] for s in styles), text)


def tok(n: float) -> str:
    n = float(n)
    for unit, div in (("B", 1e9), ("M", 1e6), ("k", 1e3)):
        if abs(n) >= div:
            v = n / div
            return "{:.1f}{}".format(v, unit) if v < 100 else "{:.0f}{}".format(v, unit)
    return "{:.0f}".format(n)


def usd(v: Optional[float]) -> str:
    if v is None:
        return "n/a"
    if abs(v) >= 1000:
        return "${:,.0f}".format(v)
    if abs(v) >= 1:
        return "${:,.2f}".format(v)
    return "${:.3f}".format(v)


def pct(v: float) -> str:
    return "{:.1f}%".format(100 * v)


def duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return "-"
    s = int(seconds)
    if s < 60:
        return "{}s".format(s)
    if s < 3600:
        return "{}m{:02d}s".format(s // 60, s % 60)
    return "{}h{:02d}m".format(s // 3600, (s % 3600) // 60)


def bar(frac: float, width: int = 20) -> str:
    frac = max(0.0, min(1.0, frac))
    full = int(frac * width)
    part = " ▏▎▍▌▋▊▉"[int((frac * width - full) * 8)]
    return ("█" * full + (part if full < width else "")).ljust(width)


def _visible_len(s: str) -> int:
    import re

    return len(re.sub(r"\033\[[0-9;]*m", "", s))


def table(rows: Sequence[Sequence[str]], align: str = "", indent: int = 2) -> str:
    """Render rows (first row = header). `align` is a string of l/r per column."""
    if not rows:
        return ""
    widths = [max(_visible_len(str(r[i])) for r in rows) for i in range(len(rows[0]))]
    out: List[str] = []
    for n, row in enumerate(rows):
        cells = []
        for i, cell in enumerate(row):
            cell = str(cell)
            pad = widths[i] - _visible_len(cell)
            a = align[i] if i < len(align) else "l"
            cells.append(" " * pad + cell if a == "r" else cell + " " * pad)
        line = " " * indent + "  ".join(cells).rstrip()
        out.append(c(line, "bold") if n == 0 else line)
    return "\n".join(out)
