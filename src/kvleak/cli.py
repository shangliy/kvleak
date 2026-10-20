"""Command-line entry point.

    kvleak                       audit ~/.claude/projects (all time)
    kvleak --since 7d            last week only
    kvleak --html report.html    shareable report
    kvleak session <id>          step-by-step timeline of one session
    kvleak advise                compact or hand off now? (newest session)
    kvleak tune                  backtest Claude Code's cache settings on your history
    kvleak tune --apply          ...and write the winners to ~/.claude/settings.json
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

SUBCOMMANDS = {"audit", "session", "advise", "tune", "breakeven", "statusline", "pointer", "models"}
TUNE_KEYS = ("autoCompactWindow", "promptCacheTtl", "subagentPromptCacheTtl")
TUNE_ENV = ("CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS",)


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
    if not args.path and not args.project and not args.since:
        _save_profile(transcripts, report)
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


def _save_profile(transcripts: List[Transcript], report) -> None:
    from .advisor import Profile

    inter = [t for t in transcripts if t.is_subagent or len(t.steps) >= 5]
    calls = sum(len(t.steps) for t in inter)
    misses = sum(1 for m in report.misses if m.cause not in ("cold_start", "subagent_cold_start"))
    Profile.from_transcripts(inter, misses, calls).save()


def _profile(args):
    from .advisor import Profile

    prof = Profile.load()
    if not prof.lengths:  # first run: learn from history once, then reuse
        transcripts = load_all(_default_paths(args), args.format, None, None)
        _save_profile(transcripts, analyze(transcripts, args.min_miss, 0))
        prof = Profile.load()
    return prof


def cmd_advise(args) -> int:
    from .advisor import advise_steps, backtest

    c = fmt.c
    if args.backtest:
        transcripts = load_all(_default_paths(args), args.format, args.since, args.project)
        prof = _profile(args)
        print(c("\n  Backtest: compact whenever the advisor says it pays (context ≥ {})".format(fmt.tok(args.min_context)), "bold"))
        rows = [["compact above", "compactions", "input spend", "saved", "share"]]
        for mc in sorted({args.min_context, 200_000, 400_000}):
            b = backtest(transcripts, prof, price_for, mc)
            rows.append([fmt.tok(mc), str(b["compactions_suggested"]), fmt.usd(b["input_cost_usd"]),
                         c(fmt.usd(b["saved_usd"]), "green"), fmt.pct(b["saved_pct"])])
        print(fmt.table(rows, "rrrrr"))
        print(c("\n  Money only: assumes the summary keeps what the rest of the session needs.", "grey"))
        return 0
    paths = _default_paths(args)
    if args.id:
        path = _find_session(paths, args.id)
    else:
        files = [f for f in discover(paths) if "/subagents/" not in f.replace(os.sep, "/")]
        path = max(files, key=os.path.getmtime) if files else None
    if not path:
        print("kvleak: no transcript found", file=sys.stderr)
        return 1
    t = load(path, args.format)
    if not t.steps:
        print("kvleak: no billed calls in {}".format(path), file=sys.stderr)
        return 1
    price = price_for(t.steps[-1].model)
    a = advise_steps(t.steps, price, _profile(args), min_context=args.min_context, now=time.time())
    money = (lambda x: fmt.usd(price.usd(x))) if price.input_per_mtok else (lambda x: fmt.tok(x) + " tok")
    print(c("\n  {} · {} · {}".format(t.session_id[:8], t.project, t.steps[-1].model), "bold"))
    print("  Context now          {}  (+{}/call)".format(fmt.tok(a.context), fmt.tok(a.growth_per_call)))
    if a.cold:
        idle = time.time() - t.steps[-1].ts.timestamp()
        print("  Cache                {}  the next message rebuilds {} ({}); compacting first reads it once at full price instead".format(
            c("cold, idle {}".format(fmt.duration(idle)), "red", "bold"), fmt.tok(a.context),
            money(a.context * price.write_mult(a.ttl))))
    print("  Carrying it costs    {} per call (cache read + {}% miss risk)".format(
        money(a.per_call_now), round(100 * _profile(args).miss_rate, 1)))
    print("  Calls so far         {}  ·  expected still to come ≈ {:.0f} (from your history)".format(
        a.calls_so_far, a.expected_remaining))
    print()
    rows = [["option", "new context", "one-time cost", "pays off after", "expected saving"]]
    rows.append(["/compact", fmt.tok(a.compact_to), money(a.compact_cost),
                 "{:.1f} calls".format(a.compact_breakeven), money(a.compact_saving)])
    rows.append(["hand-off doc + new session", fmt.tok(a.handoff_to), money(a.handoff_cost),
                 "{:.1f} calls".format(a.handoff_breakeven), money(a.handoff_saving)])
    print(fmt.table(rows, "lrrrr"))
    print()
    if a.best == "compact" and a.cold:
        print("  " + c("→ Run /compact before your next message.", "green", "bold")
              + " The cache is cold anyway; shrinking first pays for itself in {:.0f} calls.".format(a.compact_breakeven))
    elif a.best == "compact":
        print("  " + c("→ Run /compact now.", "green", "bold") + " It pays for itself in {:.0f} calls.".format(a.compact_breakeven))
    elif a.best == "handoff":
        print("  " + c("→ Write a short hand-off doc and start a fresh session", "green", "bold")
              + " (or /compact; both pay off within {:.0f} calls).".format(max(a.compact_breakeven, a.handoff_breakeven)))
    elif a.context < args.min_context:
        print("  " + c("→ Keep going.", "bold") + " Context is under {}; not worth losing detail yet.".format(fmt.tok(args.min_context)))
    else:
        print("  " + c("→ Keep going.", "bold") + " The session is likely too close to its end for compaction to pay off.")
    print(c("  Money only: compact when the summary would still carry what you need.", "grey"))
    return 0


def _settings_path() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude")) / "settings.json"


def _read_settings() -> Optional[dict]:
    """Parsed ~/.claude/settings.json; {} when missing, None when it is not valid JSON."""
    settings = _settings_path()
    if not settings.exists():
        return {}
    try:
        return json.loads(settings.read_text(encoding="utf-8"))
    except ValueError:
        print("kvleak: {} is not valid JSON; not touching it.".format(settings), file=sys.stderr)
        return None


def _write_settings(data: dict) -> Path:
    settings = _settings_path()
    if settings.exists():
        shutil.copy(settings, str(settings) + ".kvleak.bak")
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return settings


def cmd_tune(args) -> int:
    from .advisor import Profile
    from .policy import (
        IDLE_COMPACT_DEFAULT_MIN,
        best_ttl,
        idle_compaction,
        observed_ttl,
        pick_window,
        split,
        subagent_starts,
        window_sweep,
    )

    c = fmt.c
    if args.reset:
        return _reset_tune()
    since = args.since if args.since is not None else time.time() - 30 * 86400
    transcripts = load_all(_default_paths(args), args.format, since, args.project)
    if not transcripts:
        print("kvleak: no transcripts in that window", file=sys.stderr)
        return 1
    report = analyze(transcripts, args.min_miss, 0)
    groups = split(transcripts)
    main_t = groups["main"]
    calls = sum(len(t.steps) for t in main_t + groups["subagent"])
    misses = sum(1 for m in report.misses if m.cause not in ("cold_start", "subagent_cold_start"))
    prof = Profile.from_transcripts(main_t + groups["subagent"], misses, calls)
    settings = _read_settings() or {}
    env = {**settings.get("env", {}), **os.environ}
    model = max(report.model_mix, key=report.model_mix.get) if report.model_mix else None
    plan: dict = {"settings": {}, "env": {}, "rows": []}
    rows = [["setting", "now", "best", "saving / {}".format(_window_label(since)), "why"]]

    def row(name, now, best, saving, why, key=None, value=None, env_key=None):
        rows.append([name, now, c(best, "green", "bold") if best != now else best,
                     c(fmt.usd(saving), "green") if saving >= 1 else "–", why])
        plan["rows"].append({"setting": name, "now": now, "best": best, "saving_usd": round(saving, 2), "why": why,
                             "applies": bool(key or env_key)})
        if saving >= 1 and best != now:
            if key:
                plan["settings"][key] = value
            if env_key:
                plan["env"][env_key] = value

    # 1. auto-compact window: the biggest lever, because every call re-reads the whole context.
    sweep = window_sweep(main_t, price_for, prof)
    pick = pick_window(sweep, args.min_window)
    now_w = (settings.get("modelSettings", {}).get(model or "", {}).get("autoCompactWindow")
             or env.get("CLAUDE_CODE_AUTO_COMPACT_WINDOW") or settings.get("autoCompactWindow"))
    now_label = fmt.tok(parse_tokens(str(now_w))) if now_w else "auto (~967k)"
    real = sum(1 for t in main_t for s in t.steps if s.after_compaction)
    if pick:
        row("autoCompactWindow", now_label, fmt.tok(pick.window), pick.saved_usd,
            "{} compactions over {} sessions instead of {} · −{} of input spend".format(
                pick.compactions, pick.sessions, real, fmt.pct(pick.saved_pct)),
            key="autoCompactWindow", value=pick.window)
    else:
        row("autoCompactWindow", now_label, now_label, 0.0, "no window beats what you do by hand")
    plan["window_sweep"] = [vars(r) for r in sweep]

    # 2. prompt-cache TTL, main conversation vs subagents vs one-shot runs.
    for name, kind, key, env_key, label in (
        ("promptCacheTtl", "main", "promptCacheTtl", "CLAUDE_CODE_PROMPT_CACHE_TTL", "interactive sessions"),
        ("subagentPromptCacheTtl", "subagent", "subagentPromptCacheTtl", "CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL", "subagents"),
        ("`claude -p` runs: CLAUDE_CODE_PROMPT_CACHE_TTL", "headless", None, None, "one-shot runs"),
    ):
        ts = groups[kind]
        if not ts:
            continue
        sim = best_ttl(ts, price_for, "headless" if kind == "headless" else "main")
        now_ttl = env.get(env_key or "", settings.get(key or "")) if (env_key or key) else None
        now_ttl = now_ttl or observed_ttl(ts) or "5m"
        best = min(sim.values(), key=lambda r: r.cost_usd)
        other = sim["5m" if best.ttl == "1h" else "1h"]
        saving = other.cost_usd - best.cost_usd if best.ttl != now_ttl else 0.0
        why = "{} {}: {} costs {}, {} costs {}".format(
            len(ts), label, best.ttl, fmt.usd(best.cost_usd), other.ttl, fmt.usd(other.cost_usd))
        if kind == "headless":
            why += " · set it in the batch script, not globally"
            row(name, now_ttl, best.ttl, saving, why)
        else:
            row(name, now_ttl, best.ttl, saving, why, key=key, value=best.ttl)

    # 3. idle compaction threshold (Claude Code compacts idle 1h sessions itself; lower the bar?).
    if main_t:
        base = idle_compaction(main_t, price_for, IDLE_COMPACT_DEFAULT_MIN, prof.compact_floor)
        low = idle_compaction(main_t, price_for, 100_000, prof.compact_floor)
        now_min = env.get("CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS")
        now_label = fmt.tok(int(now_min)) if now_min else "200k"
        gain = low.net_usd - base.net_usd
        off = settings.get("idleCompaction") is False
        why = "{} idle gaps ≥54 min · compacting there nets {} at 200k, {} at 100k".format(
            base.idle_gaps, fmt.usd(base.net_usd), fmt.usd(low.net_usd))
        if off:
            why += " · idleCompaction is OFF in your settings"
        row("CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS", now_label, "100k" if gain >= 1 else now_label, max(gain, 0.0), why,
            env_key="CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS", value="100000")

    # 4. subagent cold starts: no setting to flip, but three knobs in the agent file.
    sub = subagent_starts(transcripts, price_for)
    if sub.count:
        why = "{} on first calls; {} of {} started with nothing cached".format(fmt.usd(sub.cost_usd), sub.cold, sub.count)
        if sub.stagger_saving_usd >= 1:
            why += " · launch one, let it warm the {} shared prefix, then the rest: ~{}".format(
                fmt.tok(sub.base), fmt.usd(sub.stagger_saving_usd))
        why += " · trim the prefix: omitClaudeMd: true, a `tools:` allowlist, or context: fork"
        row("subagent cold starts", "{} × {} avg".format(sub.count, fmt.tok(sub.avg)), "staggered", sub.stagger_saving_usd, why)

    total = sum(r["saving_usd"] for r in plan["rows"] if r["applies"])
    manual = sum(r["saving_usd"] for r in plan["rows"] if not r["applies"])
    if args.json:
        print(json.dumps(plan, indent=2, sort_keys=True))
        return 0
    print(c("\n  kvleak tune · {} sessions + {} subagents + {} one-shot runs · {} input spend over the last {}".format(
        len(main_t), len(groups["subagent"]), f"{len(groups['headless']):,}", fmt.usd(report.cost_usd),
        _window_label(since)), "bold"))
    print(c("  Each setting replayed on your own transcripts. Money only; compaction also loses detail.\n", "grey"))
    print(fmt.table(rows, "lllrl"))
    print()
    if plan["settings"] or plan["env"]:
        print("  Total if applied: " + c(fmt.usd(total), "green", "bold") + " per {}".format(_window_label(since))
              + (c("  (+{} from the habits above)".format(fmt.usd(manual)), "grey") if manual >= 1 else ""))
        if args.apply:
            return _apply_tune(plan)
        print("  Write these to {}: ".format(_settings_path()) + c("kvleak tune --apply", "bold")
              + c("  (undo: kvleak tune --reset)", "grey"))
    else:
        print("  Your settings already match your history. Nothing to change.")
    return 0


def _window_label(since: float) -> str:
    days = max(int(round((time.time() - since) / 86400)), 1)
    return "{}d".format(days)


def _apply_tune(plan: dict) -> int:
    data = _read_settings()
    if data is None:
        return 1
    data.update(plan["settings"])
    if plan["env"]:
        data.setdefault("env", {}).update(plan["env"])
    settings = _write_settings(data)
    for k, v in plan["settings"].items():
        print("  wrote {} = {}".format(k, json.dumps(v)))
    for k, v in plan["env"].items():
        print("  wrote env.{} = {}".format(k, json.dumps(v)))
    print("  → {}  (restart Claude Code; undo with kvleak tune --reset)".format(settings))
    return 0


def _reset_tune() -> int:
    data = _read_settings()
    if not data:
        print("kvleak: nothing to reset")
        return 0
    removed = [k for k in TUNE_KEYS if data.pop(k, None) is not None]
    env = data.get("env", {})
    removed += ["env." + k for k in TUNE_ENV if env.pop(k, None) is not None]
    if not env and "env" in data:
        del data["env"]
    if removed:
        _write_settings(data)
        print("Removed {} from {}".format(", ".join(removed), _settings_path()))
    else:
        print("kvleak: no tuned settings found")
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
    data = _read_settings()
    if data is None:
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
    data["statusLine"] = {"type": "command", "command": command, "padding": 0}
    settings = _write_settings(data)
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

    ad = sub.add_parser("advise", help="should I /compact or hand off now? (newest session by default)")
    _add_common(ad)
    ad.add_argument("id", nargs="?", help="session id (prefix ok) or transcript path")
    ad.add_argument("--min-context", type=parse_tokens, default=100_000, metavar="TOKENS",
                    help="never suggest compacting below this context size (default 100k)")
    ad.add_argument("--backtest", action="store_true", help="replay your history with the advisor in the loop")
    ad.set_defaults(func=cmd_advise)

    tu = sub.add_parser("tune", help="backtest Claude Code's cache settings on your history (last 30d by default)")
    _add_common(tu)
    tu.add_argument("--min-window", type=parse_tokens, default=150_000, metavar="TOKENS",
                    help="never recommend auto-compacting below this context size (default 150k)")
    tu.add_argument("--apply", action="store_true", help="write the recommended settings to ~/.claude/settings.json")
    tu.add_argument("--reset", action="store_true", help="remove every setting kvleak tune wrote")
    tu.add_argument("--json", action="store_true", help="machine-readable plan")
    tu.set_defaults(func=cmd_tune)

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
    data = _read_settings()
    if not data:
        print("kvleak: nothing to uninstall")
        return 0
    if "kvleak" in json.dumps(data.get("statusLine", "")):
        del data["statusLine"]
        settings = _write_settings(data)
        print("Removed kvleak statusline from {}".format(settings))
    else:
        print("kvleak statusline is not installed")
    return 0
