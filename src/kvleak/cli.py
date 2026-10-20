"""Command-line entry point.

    kvleak                       audit ~/.claude/projects (all time)
    kvleak --since 7d            last week only
    kvleak --html report.html    shareable report
    kvleak session <id>          step-by-step timeline of one session
    kvleak breakeven 150k        should I keep this cache alive?
    kvleak statusline --install  live TTL countdown in Claude Code
    kvleak pointer build.log     compact hand-off message for multi-agent boards
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from . import __version__, fmt
from .analyze import CAUSES, analyze, classify, step_cost
from .pricing import TTL_1H, TTL_5M, price_for
from .pricing import table as price_table
from .sources import Transcript, default_claude_root, discover, load

SUBCOMMANDS = {"audit", "session", "breakeven", "statusline", "pointer", "models"}


# ------------------------------------------------------------------------- helpers


def parse_since(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    m = re.fullmatch(r"(\d+)\s*([mhdw])", value.strip().lower())
    if m:
        n, unit = int(m.group(1)), m.group(2)
        return time.time() - n * {"m": 60, "h": 3600, "d": 86400, "w": 604800}[unit]
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError("--since takes e.g. 7d, 24h, 2w or 2026-10-01") from None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp()


def parse_tokens(value: str) -> int:
    m = re.fullmatch(r"([\d.]+)\s*([kKmM]?)", value.strip().replace(",", "").replace("_", ""))
    if not m:
        raise argparse.ArgumentTypeError("expected a token count like 150000, 150k or 1.2M")
    return int(float(m.group(1)) * {"": 1, "k": 1e3, "m": 1e6}[m.group(2).lower()])


def _load_one(args):
    path, fmt_, since = args
    try:
        t = load(path, fmt_)
    except OSError:
        return None
    if since is not None:
        t.steps = [s for s in t.steps if s.ts is None or s.ts.timestamp() >= since]
    return t


def load_all(paths: List[str], fmt_: str, since: Optional[float], project: Optional[str]) -> List[Transcript]:
    files = sorted(discover(paths, since))
    if project:
        files = [f for f in files if project.lower() in f.lower()]
    jobs = [(f, fmt_, since) for f in files]
    if len(jobs) > 64:
        with ProcessPoolExecutor() as ex:
            results = list(ex.map(_load_one, jobs, chunksize=32))
    else:
        results = [_load_one(j) for j in jobs]
    return [t for t in results if t and t.steps]


def _default_paths(args) -> List[str]:
    if args.path:
        return args.path
    root = default_claude_root()
    if not root.exists():
        sys.exit(
            "kvleak: no Claude Code transcripts at {}.\n"
            "Point me at your logs with --path <dir-or-file> (any JSONL with token usage works).".format(root)
        )
    return [str(root)]


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--path", action="append", help="transcript file or directory (repeatable). Default: ~/.claude/projects")
    p.add_argument("--format", default="auto", choices=["auto", "claude", "generic"], help="log format (default: auto-detect)")
    p.add_argument("--since", type=parse_since, help="only look at activity since 7d / 24h / 2026-10-01")
    p.add_argument("--project", help="only sessions whose path contains this text")
    p.add_argument("--min-miss", type=parse_tokens, default=10_000, metavar="TOKENS",
                   help="ignore misses smaller than this many uncached tokens (default 10k)")
    p.add_argument("--no-color", action="store_true", help="disable ANSI colors")


# ------------------------------------------------------------------------ commands


def cmd_audit(args) -> int:
    transcripts = load_all(_default_paths(args), args.format, args.since, args.project)
    report = analyze(transcripts, args.min_miss, args.top)
    wrote_file = False
    if args.html:
        from .htmlreport import render_html

        Path(args.html).write_text(render_html(report), encoding="utf-8")
        print("Wrote {}".format(args.html), file=sys.stderr)
        wrote_file = True
    if args.markdown:
        from .report import render_markdown

        Path(args.markdown).write_text(render_markdown(report), encoding="utf-8")
        print("Wrote {}".format(args.markdown), file=sys.stderr)
        wrote_file = True
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True, default=str))
    elif not wrote_file or sys.stdout.isatty():
        from .report import render_terminal

        print(render_terminal(report, args.top_misses))
    if args.fail_over is not None and report.cost and report.leak / report.cost * 100 > args.fail_over:
        return 2
    return 0


def _find_session(paths: List[str], needle: str) -> Optional[str]:
    if os.path.isfile(needle):
        return needle
    for f in discover(paths):
        if needle in os.path.basename(f) or needle in f:
            return f
    return None


def cmd_session(args) -> int:
    path = _find_session(_default_paths(args), args.id)
    if not path:
        print("kvleak: no transcript matching {!r}".format(args.id), file=sys.stderr)
        return 1
    t = load(path, args.format)
    misses = {m.step_index: m for m in classify(t, args.min_miss)}
    c = fmt.c
    print(c("\n {} ".format(t.session_id), "bold") + c(path, "grey"))
    rows = [["#", "time", "gap", "model", "read", "uncached", "hit", "cost", ""]]
    prev_ts = None
    for i, s in enumerate(t.steps):
        gap = (s.ts - prev_ts).total_seconds() if (s.ts and prev_ts) else None
        prev_ts = s.ts or prev_ts
        m = misses.get(i)
        if args.misses_only and not m:
            continue
        price = price_for(s.model)
        cost = step_cost(s, price)
        hit = s.read / s.prompt if s.prompt else 0
        tag = c("◀ " + CAUSES[m.cause], "red", "bold") if m else ""
        rows.append([
            str(i),
            s.ts.strftime("%m-%d %H:%M:%S") if s.ts else "-",
            c(fmt.duration(gap), "yellow") if gap and gap >= 270 else fmt.duration(gap),
            (s.model or "?").replace("claude-", ""),
            fmt.tok(s.read),
            fmt.tok(s.uncached),
            c(fmt.pct(hit), "green" if hit > 0.8 else "red"),
            fmt.usd(price.usd(cost)) if price.input_per_mtok else fmt.tok(cost),
            tag,
        ])
    print(fmt.table(rows, "llrlrrrrl"))
    total_pen = sum(m.penalty for m in misses.values())
    p = price_for(t.steps[0].model if t.steps else None)
    print("\n  {} heavy misses · leak {}".format(
        len(misses), fmt.usd(p.usd(total_pen)) if p.input_per_mtok else fmt.tok(total_pen) + " tok"))
    return 0


def cmd_breakeven(args) -> int:
    price = price_for(args.model)
    if price.input_per_mtok is None:
        print("kvleak: unknown model {!r}; using default multipliers".format(args.model), file=sys.stderr)
    ttl = TTL_1H if args.ttl == "1h" else TTL_5M
    write = price.write_1h_mult if ttl == TTL_1H else price.write_5m_mult
    n = args.tokens
    penalty = n * (write - price.read_mult)
    ping = n * price.read_mult
    kmax = price.keepalive_kmax(ttl)
    interval = ttl - 30
    show = lambda x: "{} tok-equiv{}".format(fmt.tok(x), "  ({})".format(fmt.usd(price.usd(x))) if price.input_per_mtok else "")  # noqa: E731
    c = fmt.c
    print()
    print(c("  Cached prefix {} on {} ({} TTL)".format(fmt.tok(n), args.model, args.ttl), "bold"))
    print("  Rebuild if it expires   {}".format(show(n * write)))
    print("  ...of which avoidable   {}".format(c(show(penalty), "red")))
    print("  One keep-alive ping     {}".format(c(show(ping), "green")))
    print("  Break-even              {} pings ≈ {} of idle time".format(
        c(str(kmax), "bold"), fmt.duration(kmax * interval + ttl)))
    print()
    if args.idle:
        idle = parse_since_seconds(args.idle)
        pings = max(0, -(-(idle - ttl) // interval)) if idle > ttl else 0
        if pings == 0:
            print("  Idle {}: cache survives on its own. Just reply in time.".format(fmt.duration(idle)))
        elif pings <= kmax:
            print("  Idle {}: {} pings cost {}, saving {}. {}".format(
                fmt.duration(idle), pings, show(pings * ping), show(penalty - pings * ping), c("Keep it alive.", "green", "bold")))
        else:
            print("  Idle {}: would need {} pings (> {}). {}".format(
                fmt.duration(idle), pings, kmax, c("Let it expire.", "yellow", "bold")))
        print()
    return 0


def parse_since_seconds(value: str) -> int:
    m = re.fullmatch(r"(\d+)\s*([smh]?)", value.strip().lower())
    if not m:
        raise SystemExit("kvleak: --idle takes e.g. 20m, 90s or 2h")
    return int(m.group(1)) * {"": 60, "s": 1, "m": 60, "h": 3600}[m.group(2)]


def cmd_statusline(args) -> int:
    if not args.install:
        from .statusline import main as sl_main

        return sl_main()
    settings = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude")) / "settings.json"
    data = {}
    if settings.exists():
        try:
            data = json.loads(settings.read_text(encoding="utf-8"))
        except ValueError:
            print("kvleak: {} is not valid JSON; not touching it.".format(settings), file=sys.stderr)
            return 1
    exe = shutil.which("kvleak")
    if exe:
        command = "{} statusline".format(exe)
    elif shutil.which("uvx"):  # running from an ephemeral `uvx kvleak` env; don't pin its path
        command = "uvx kvleak statusline"
    else:
        command = "{} -m kvleak statusline".format(sys.executable)
    existing = data.get("statusLine")
    if existing and "kvleak" not in json.dumps(existing) and not args.force:
        print("kvleak: you already have a statusLine:\n  {}\nRe-run with --force to replace it, or add\n  {}\n"
              "to your own statusline script.".format(json.dumps(existing), command))
        return 1
    if settings.exists():
        shutil.copy(settings, str(settings) + ".kvleak.bak")
    data["statusLine"] = {"type": "command", "command": command, "padding": 0}
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print("Installed. Claude Code will show the cache countdown on its next refresh.\n"
          "  settings: {}\n  undo:     kvleak statusline --uninstall".format(settings))
    return 0


def cmd_pointer(args) -> int:
    from .pointer import dumps, make_pointer

    print(dumps(make_pointer(args.file, tail=args.tail, status=args.status, note=args.note)))
    return 0


def cmd_models(args) -> int:
    rows = [["model", "input $/MTok", "read", "write 5m", "write 1h", "keep-alive break-even (5m)"]]
    for name, p in sorted(price_table().items()):
        rows.append([name, fmt.usd(p.input_per_mtok) if p.input_per_mtok is not None else "?",
                     "{:g}x".format(p.read_mult), "{:g}x".format(p.write_5m_mult),
                     "{:g}x".format(p.write_1h_mult), "{} pings".format(p.keepalive_kmax())])
    print(fmt.table(rows, "lrrrrr"))
    return 0


# --------------------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="kvleak",
        description="Find where your AI agent leaks prompt-cache money, and how to stop it.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("\n", 1)[1],
    )
    p.add_argument("--version", action="version", version="kvleak " + __version__)
    sub = p.add_subparsers(dest="cmd")

    a = sub.add_parser("audit", help="audit transcripts (default command)")
    _add_common(a)
    a.add_argument("--json", action="store_true", help="machine-readable output")
    a.add_argument("--html", metavar="FILE", help="write a self-contained HTML report")
    a.add_argument("--markdown", metavar="FILE", help="write a Markdown summary (great for PRs / Slack)")
    a.add_argument("--top", type=int, default=10, help="sessions to list (default 10)")
    a.add_argument("--top-misses", type=int, default=5, help="single misses to list (default 5)")
    a.add_argument("--fail-over", type=float, metavar="PCT",
                   help="exit 2 if more than PCT%% of input spend leaked (for CI)")
    a.set_defaults(func=cmd_audit)

    s = sub.add_parser("session", help="timeline of one session")
    _add_common(s)
    s.add_argument("id", help="session id (prefix ok) or transcript path")
    s.add_argument("--misses-only", action="store_true")
    s.set_defaults(func=cmd_session)

    b = sub.add_parser("breakeven", help="keep-alive vs. rebuild calculator")
    b.add_argument("tokens", type=parse_tokens, help="cached prefix size, e.g. 150k")
    b.add_argument("--model", default="claude-opus-5-5")
    b.add_argument("--ttl", choices=["5m", "1h"], default="5m")
    b.add_argument("--idle", help="planned idle time, e.g. 20m")
    b.add_argument("--no-color", action="store_true")
    b.set_defaults(func=cmd_breakeven)

    sl = sub.add_parser("statusline", help="Claude Code statusline (reads JSON on stdin)")
    sl.add_argument("--install", action="store_true", help="add kvleak to ~/.claude/settings.json")
    sl.add_argument("--uninstall", action="store_true", help="remove it again")
    sl.add_argument("--force", action="store_true", help="replace an existing statusLine")
    sl.set_defaults(func=cmd_statusline)

    pt = sub.add_parser("pointer", help="compact hand-off pointer for a big file")
    pt.add_argument("file")
    pt.add_argument("--tail", type=int, default=3, help="include the last N lines (default 3)")
    pt.add_argument("--status", help="e.g. PASS / FAIL")
    pt.add_argument("--note", help="one-line summary")
    pt.set_defaults(func=cmd_pointer)

    m = sub.add_parser("models", help="show the price table")
    m.set_defaults(func=cmd_models)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # bars and rings must not crash cp1252 consoles
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or (argv[0] not in SUBCOMMANDS and argv[0] not in ("-h", "--help", "--version")):
        argv.insert(0, "audit")
    args = build_parser().parse_args(argv)
    if getattr(args, "no_color", False):
        fmt.set_color(False)
    if args.cmd == "statusline" and args.uninstall:
        return _uninstall_statusline()
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        return 0


def _uninstall_statusline() -> int:
    settings = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude")) / "settings.json"
    try:
        data = json.loads(settings.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print("kvleak: nothing to uninstall")
        return 0
    if "kvleak" in json.dumps(data.get("statusLine", "")):
        del data["statusLine"]
        settings.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        print("Removed kvleak statusline from {}".format(settings))
    else:
        print("kvleak statusline is not installed")
    return 0
