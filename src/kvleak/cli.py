"""Command-line entry point.

    kvleak                       audit ~/.claude/projects (all time)
    kvleak --since 7d            last week only
    kvleak --html report.html    shareable report
    kvleak session <id>          step-by-step timeline of one session
    kvleak advise                compact or hand off now? (newest session)
    kvleak tune                  backtest Claude Code's cache settings on your history
    kvleak tune --apply          ...and write the winners to ~/.claude/settings.json
    kvleak diet                  what filled the context, and what each piece cost to carry
    kvleak skills                what the skill listing costs on every call, and which loaded skills cost to carry
    kvleak hooks --install      keep long shell output out of the context; make compaction lossless
    kvleak recall "<regex>"      search the history a compaction dropped
    kvleak refine --apply        write evidence-backed context-hygiene rules to ~/.claude/CLAUDE.md
    kvleak refine --probe        did the rules change behaviour? before/after contrast
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
from typing import List, Optional, Tuple

from . import __version__, fmt
from .analyze import CAUSES, analyze, classify, step_cost
from .pricing import TTL_1H, TTL_5M, price_for
from .pricing import table as price_table
from .sources import Transcript, default_claude_root, discover, load, load_many

SUBCOMMANDS = {"audit", "session", "advise", "tune", "diet", "hook", "hooks", "recall", "refine", "keepalive", "whatif", "probe", "skills", "breakeven",
               "statusline", "pointer", "models"}
TUNE_KEYS = ("autoCompactWindow", "promptCacheTtl", "subagentPromptCacheTtl", "bashOutputMaxChars")
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


def _load_one(args) -> List[Transcript]:
    path, fmt_, since = args
    try:
        found = load_many(path, fmt_)
    except OSError:
        return []
    if since is not None:
        for t in found:
            t.steps = [s for s in t.steps if s.ts is None or s.ts.timestamp() >= since]
    return found


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
    out = [t for found in results for t in found if t.steps]
    if project:
        out = [t for t in out if project.lower() in t.path.lower() or project.lower() in t.project.lower()]
    return out


def _default_paths(args) -> List[str]:
    from .readers import default_roots

    if args.path:
        return args.path
    roots = default_roots()
    if not roots:
        sys.exit(
            "kvleak: no session stores found (looked for Claude Code at {}, Codex CLI, Gemini CLI and OpenCode).\n"
            "Point me at your logs with --path <dir-or-file> (any JSONL with token usage works).".format(default_claude_root())
        )
    return roots


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


def cmd_diet(args) -> int:
    from .diet import SYSTEM_KIND, aggregate, kind_label, read_diet, tips

    c = fmt.c
    miss = _profile(args).miss_rate
    if args.all:
        since = args.since if args.since is not None else time.time() - 30 * 86400
        transcripts = load_all(_default_paths(args), args.format, since, args.project)
        agg = aggregate(transcripts, miss)
        if not agg.kinds:
            print("kvleak: no interactive sessions in that window", file=sys.stderr)
            return 1
        kinds, top = agg.kinds, agg.top[:args.top]
        total = sum(v["tokens"] for v in kinds.values())
        print(c("\n  What filled the context · {} sessions · {} calls · {} tokens entered · carrying it cost {}".format(
            agg.sessions, f"{agg.calls:,}", fmt.tok(total), fmt.usd(sum(v["carry_usd"] for v in kinds.values()))), "bold"))
        _diet_table(kinds, total, args.top)
        if top:
            print(c("\n  Costliest single items", "bold"))
            rows = [["session", "source", "what", "tokens", "carried", "cost"]]
            for sid, b in top:
                rows.append([sid[:8], kind_label(b.kind), b.label, fmt.tok(b.tokens), "{} calls".format(b.carried), fmt.usd(b.carry_usd)])
            print(fmt.table(rows, "lllrrr"))
        _diet_turns(agg.long_turns, agg.long_turn_calls, agg.history_usd, agg.subagent_saving_usd, None)
        _diet_chains(agg.chains, agg.chain_calls, agg.chain_saving_usd, agg.chain_examples, agg.calls)
        _diet_thinking(agg.output_tokens, agg.thinking_tokens, agg.effort)
    else:
        paths = _default_paths(args)
        if args.id:
            path = _find_session(paths, args.id)
        else:
            files = [f for f in discover(paths) if "/subagents/" not in f.replace(os.sep, "/")]
            path = max(files, key=os.path.getmtime) if files else None
        if not path:
            print("kvleak: no transcript found", file=sys.stderr)
            return 1
        d = read_diet(path, miss_rate=miss)
        if not d.calls:
            print("kvleak: no billed calls in {}".format(path), file=sys.stderr)
            return 1
        kinds = d.by_kind()
        total = d.total_tokens or 1
        print(c("\n  What filled the context · {} · {} calls · peak {} · {} compactions · carrying it cost {}".format(
            d.session_id[:8], d.calls, fmt.tok(d.peak), d.compactions, fmt.usd(d.carry_usd)), "bold"))
        _diet_table(kinds, total, args.top)
        top = sorted((b for b in d.blocks if b.kind != SYSTEM_KIND), key=lambda b: -b.carry_usd)[:args.top]
        if top:
            print(c("\n  Costliest single items", "bold"))
            rows = [["call", "source", "what", "tokens", "carried", "cost"]]
            for b in top:
                rows.append([str(b.call), kind_label(b.kind), b.label + (" (image)" if b.is_image else ""), fmt.tok(b.tokens),
                             "{} calls".format(b.carried), fmt.usd(b.carry_usd)])
            print(fmt.table(rows, "rllrrr"))
        long = d.long_turns
        _diet_turns(len(long), sum(t.calls for t in long), sum(t.history_usd for t in long),
                    sum(t.subagent_saving_usd for t in long), long[:5])
        _diet_chains(len(d.chains), sum(ch.calls for ch in d.chains), sum(ch.saving_usd for ch in d.chains),
                     [(d.session_id, ch) for ch in d.chains[:5]], d.calls)
        _diet_thinking(d.output_tokens, d.thinking_tokens, d.effort)
    print(c("\n  How to shrink it", "bold"))
    for i, tip in enumerate(tips(kinds), 1):
        print("  {}. {}".format(i, tip))
    print(c("  Tokens are calibrated to the API's usage numbers (thinking exact, the rest by size; images by pixels). "
            "Carried cost = tokens × calls it stayed in the context × cache-read price.\n"
            "  Keep shell output out of the context automatically: kvleak hooks --install · turn this into rules: kvleak refine", "grey"))
    return 0


def _diet_chains(n: int, calls: int, saving_usd: float, examples, total_calls: int) -> None:
    from .diet import CHAIN_MIN

    c = fmt.c
    if not n:
        return
    print(c("\n  Mechanical chains (≥{} consecutive ls/cat/grep/read steps with no real decision between them)".format(CHAIN_MIN), "bold"))
    print("  {} chains · {} of {} calls · each step re-read the whole context · as single scripts: up to {}".format(
        n, f"{calls:,}", f"{total_calls:,}", c("−" + fmt.usd(saving_usd), "green", "bold")))
    if examples:
        rows = [["session", "starts at call", "steps", "context then", "what they did", "as one script"]]
        for sid, ch in examples:
            rows.append([sid[:8], str(ch.start), str(ch.calls), fmt.tok(ch.context), " · ".join(ch.labels[:3])[:60], "−" + fmt.usd(ch.saving_usd)])
        print(fmt.table(rows, "lrrrlr"))


def _diet_table(kinds: dict, total: float, top: int) -> None:
    from .diet import kind_label

    rows = [["source", "items", "tokens", "share", "biggest", "carried cost"]]
    for k, v in list(kinds.items())[:max(top, 8)]:
        rows.append([kind_label(k), f"{v['count']:,}", fmt.tok(v["tokens"]), fmt.pct(v["tokens"] / total),
                     fmt.tok(v["max"]), fmt.usd(v["carry_usd"])])
    print(fmt.table(rows, "lrrrrr"))


def _diet_turns(n: int, calls: int, history_usd: float, saving_usd: float, examples) -> None:
    from .diet import LONG_TURN

    c = fmt.c
    if not n:
        return
    print(c("\n  Long turns (≥{} calls before you spoke again)".format(LONG_TURN), "bold"))
    print("  {} turns · {} calls · re-reading the history they started with cost {} · in fresh subagents: {}".format(
        n, f"{calls:,}", fmt.usd(history_usd), c("−" + fmt.usd(saving_usd), "green", "bold")))
    if examples:
        rows = [["starts at call", "calls", "context then", "history re-read", "as a subagent"]]
        for t in examples:
            rows.append([str(t.start), str(t.calls), fmt.tok(t.context), fmt.usd(t.history_usd), "−" + fmt.usd(t.subagent_saving_usd)])
        print(fmt.table(rows, "rrrrr"))


def _diet_thinking(output: int, thinking: int, effort: dict) -> None:
    c = fmt.c
    if not output or not thinking:
        return
    parts = []
    for (model, level), (n, th) in sorted(effort.items(), key=lambda kv: -kv[1][0])[:4]:
        if n >= 20:
            parts.append("{} at {}: {:.0f}/call".format(model.replace("claude-", ""), level, th / n))
    print(c("\n  Thinking", "bold") + "  {} of {} output tokens ({}); kept in context until compaction on current models{}".format(
        fmt.tok(thinking), fmt.tok(output), fmt.pct(thinking / output), (" · " + " · ".join(parts)) if parts else ""))


def _settings_path(project: bool = False) -> Path:
    if project:
        return Path.cwd() / ".claude" / "settings.json"
    return Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude")) / "settings.json"


def _read_settings(project: bool = False) -> Optional[dict]:
    """Parsed ~/.claude/settings.json; {} when missing, None when it is not valid JSON."""
    settings = _settings_path(project)
    if not settings.exists():
        return {}
    try:
        return json.loads(settings.read_text(encoding="utf-8"))
    except ValueError:
        print("kvleak: {} is not valid JSON; not touching it.".format(settings), file=sys.stderr)
        return None


def _write_settings(data: dict, project: bool = False) -> Path:
    settings = _settings_path(project)
    if settings.exists():
        shutil.copy(settings, str(settings) + ".kvleak.bak")
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return settings


def _tune_plan(args, transcripts: List[Transcript], since: float):
    """Replay every Claude Code cache setting on these transcripts. Returns (plan, table rows, main sessions, groups, report)."""
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
    transcripts = [t for t in transcripts if t.source == "claude"]  # these are Claude Code's settings
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

    # 4. what enters the context per call: Bash output cap and effort (thinking kept in context).
    from .diet import THINKING_KIND, aggregate

    agg = aggregate(main_t, prof.miss_rate)
    bash = agg.kinds.get("Bash")
    if bash and bash["count"]:
        now_cap = int(env.get("BASH_MAX_OUTPUT_LENGTH") or settings.get("bashOutputMaxChars") or 30_000)
        best_cap, best_gain = now_cap, 0.0
        for cap in (16_000, 8_000):
            if cap >= now_cap:
                continue
            gain = _bash_cap_saving(agg, cap)
            if gain > best_gain:
                best_cap, best_gain = cap, gain
        row("bashOutputMaxChars", f"{now_cap:,}", f"{best_cap:,}", best_gain,
            "Bash results are {} of what entered the context; longer output goes to a file the model can still read".format(
                fmt.pct(bash["tokens"] / max(sum(v["tokens"] for v in agg.kinds.values()), 1))),
            key="bashOutputMaxChars", value=best_cap)
    think = agg.kinds.get(THINKING_KIND)
    if think and agg.thinking_tokens:
        carry_per_tok = think["carry_usd"] / max(think["tokens"], 1)
        for mdl in {m for m, _ in agg.effort}:
            levels = {lvl: (n, th / n) for (m, lvl), (n, th) in agg.effort.items() if m == mdl and n >= 50}
            if "high" in levels and "medium" in levels and levels["high"][1] > levels["medium"][1]:
                n_high, per_high = levels["high"]
                delta = (per_high - levels["medium"][1]) * n_high
                p = price_for(mdl)
                saving = (p.usd(delta * p.output_mult) or 0.0) + delta * carry_per_tok
                row("effortLevel ({})".format(mdl.replace("claude-", "")), "high on {:,} calls".format(n_high), "medium", saving,
                    "thinking {:.0f}/call at high vs {:.0f} at medium, billed 5× then re-read until compaction · quality trade-off: "
                    "set `effort:` per skill/subagent instead".format(per_high, levels["medium"][1]))

    # 5. subagent cold starts: no setting to flip, but three knobs in the agent file.
    sub = subagent_starts(transcripts, price_for)
    if sub.count:
        why = "{} on first calls; {} of {} started with nothing cached".format(fmt.usd(sub.cost_usd), sub.cold, sub.count)
        if sub.stagger_saving_usd >= 1:
            why += " · launch one, let it warm the {} shared prefix, then the rest: ~{}".format(
                fmt.tok(sub.base), fmt.usd(sub.stagger_saving_usd))
        why += " · trim the prefix: omitClaudeMd: true, a `tools:` allowlist, or context: fork"
        row("subagent cold starts", "{} × {} avg".format(sub.count, fmt.tok(sub.avg)), "staggered", sub.stagger_saving_usd, why)

    return plan, rows, main_t, groups, report


def cmd_tune(args) -> int:
    c = fmt.c
    if args.reset:
        return _reset_tune()
    since = args.since if args.since is not None else time.time() - 30 * 86400
    transcripts = load_all(_default_paths(args), args.format, since, args.project)
    if not transcripts:
        print("kvleak: no transcripts in that window", file=sys.stderr)
        return 1
    plan, rows, main_t, groups, report = _tune_plan(args, transcripts, since)
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


def cmd_whatif(args) -> int:
    """Replay other tools' promises on your own bill."""
    from .diet import INPUT_SUFFIX, PLAIN_KINDS, SYSTEM_KIND, TEXT_KIND, THINKING_KIND, aggregate
    from .policy import split

    c = fmt.c
    since = args.since if args.since is not None else time.time() - 30 * 86400
    transcripts = load_all(_default_paths(args), args.format, since, args.project)
    if not transcripts:
        print("kvleak: no transcripts in that window", file=sys.stderr)
        return 1
    report = analyze(transcripts, args.min_miss, 0)
    if not report.priced or not report.cost_usd:
        print("kvleak: no priced calls in that window (add a pricing file for your models)", file=sys.stderr)
        return 1
    bill = report.cost_usd + report.output_usd
    uncached_usd = report.cost_usd - report.read_usd - report.write_usd
    window = _window_label(since)
    print(c("\n  What if · {} model calls · {} over the last {}".format(f"{report.steps:,}", fmt.usd(bill), window), "bold"))
    print("  Bill = {} uncached input + {} cache writes + {} cache reads + {} output".format(
        fmt.usd(uncached_usd), fmt.usd(report.write_usd), fmt.usd(report.read_usd), fmt.usd(report.output_usd)))
    print(c("  Each lever below is what its tool advertises, applied to the part of this bill it can touch. Upper bounds: "
            "they assume the model never fetches the trimmed text again.", "grey"))
    claude_t = [t for t in transcripts if t.source == "claude"]
    main_t = split(claude_t)["main"]
    agg = aggregate(main_t, _profile(args).miss_rate) if main_t else None
    kinds = agg.kinds if agg else {}
    model = max(report.model_mix, key=report.model_mix.get) if report.model_mix else None
    price = price_for(model)

    def kind_usd(pred) -> float:
        return sum(v["carry_usd"] + v["entry_usd"] for k, v in kinds.items() if pred(k))

    rows: List[Tuple[str, str, float]] = []
    rows.append(("Shell output −60%", "rtk's low end (\"60–90%\")", 0.6 * kind_usd(lambda k: k == "Bash")))
    rows.append(("All tool results −40%", "AgentDiet, arXiv 2509.23586",
                 0.4 * kind_usd(lambda k: k not in PLAIN_KINDS and not k.endswith(INPUT_SUFFIX))))
    rows.append(("Startup context −50%", "claude-token-optimizer", 0.5 * kind_usd(lambda k: k == SYSTEM_KIND)))
    text = kinds.get(TEXT_KIND, {})
    text_out = price.usd(text.get("tokens", 0) * price.output_mult) or 0.0
    rows.append(("Replies half as long", "caveman", 0.5 * (kind_usd(lambda k: k == TEXT_KIND) + text_out)))
    think_out = (price.usd(agg.thinking_tokens * price.output_mult) or 0.0) if agg else 0.0
    rows.append(("Thinking −25%", "effort high → medium", 0.25 * (kind_usd(lambda k: k == THINKING_KIND) + think_out)))
    tune_total = 0.0
    if main_t:
        plan, _, _, _, _ = _tune_plan(args, claude_t, since)
        tune_total = sum(r["saving_usd"] for r in plan["rows"] if r["applies"])
        rows.append(("kvleak tune --apply", "compaction window, TTLs, idle compaction, Bash cap", tune_total))
    if claude_t:
        from .skills import installed
        from .skills import report as skills_report

        sk = skills_report(claude_t, installed(), _profile(args).miss_rate)
        if sk.rows:
            rows.append(("Unused skills off", "kvleak skills: {} of {} never invoked".format(len(sk.unused), len(sk.rows)),
                         sk.unused_usd + sk.repeat_usd))
    table = [["if you…", "who promises it", "bill saving", "share of bill"]]
    for name, who, saving in sorted(rows, key=lambda r: -r[2]):
        table.append([name, who, c("−" + fmt.usd(saving), "green" if saving >= 1 else "grey"), fmt.pct(saving / bill)])
    print()
    print(fmt.table(table, "lllr"))
    print()
    print(c("  Tool-output compressors only shrink what the context carries, and carrying is paid at the cache-read price. "
            "On 2,848 billed Claude Code runs a 38% cut in tool output raised cost 6.8% (arXiv 2607.12161); "
            "a replay over 614M tokens put rtk at 0.5% of the bill.\n"
            "  Compaction changes how much is carried at all, which is why kvleak tune's levers are larger.", "grey"))
    if not main_t and claude_t == []:
        print(c("  No Claude Code sessions in the window: the context levers need them; the bill above is from "
                "{}.".format(", ".join(sorted({t.source for t in transcripts}))), "grey"))
    return 0


def cmd_skills(args) -> int:
    """What the skill listing costs on every call, and what loaded skill bodies cost to carry."""
    from .skills import disable_hint, installed, report

    c = fmt.c
    since = args.since if args.since is not None else time.time() - 30 * 86400
    transcripts = [t for t in load_all(_default_paths(args), args.format, since, args.project) if t.source == "claude"]
    if not transcripts:
        print("kvleak: no Claude Code transcripts in that window", file=sys.stderr)
        return 1
    skills = installed()
    rep = report(transcripts, skills, _profile(args).miss_rate)
    if args.json:
        print(json.dumps(rep.to_dict(), indent=1))
        return 0
    window = _window_label(since)
    print(c("\n  kvleak skills · {} skills listed · {} sessions · {} model calls over the last {}".format(
        len(rep.rows), f"{rep.sessions:,}", f"{rep.calls:,}", window), "bold"))
    if not rep.rows:
        print("  No SKILL.md found under {} (skills/, enabled plugins) or bundled with Claude Code.".format(
            os.environ.get("CLAUDE_CONFIG_DIR", "~/.claude")))
    print("  Listed in every call: {} tokens of skill descriptions ≈ {} / {} (written at each session start, read on every call)".format(
        fmt.tok(rep.listing_tokens), c(fmt.usd(rep.listing_usd), "yellow", "bold"), window))
    print("  Loaded bodies: {} carried through their sessions ≈ {}{}".format(
        f"{sum(r.invocations for r in rep.rows if r.carried_usd):,} injections", c(fmt.usd(rep.body_usd), "yellow", "bold"),
        " · {} repeat injections in the same session ≈ {}".format(rep.repeats, fmt.usd(rep.repeat_usd)) if rep.repeats else ""))
    print()
    rows = [["skill", "source", "listing", "body", "invoked", "sessions", "body carried", "repeats"]]
    for r in rep.rows:
        rows.append([r.name[:30], r.source, fmt.tok(r.desc_tokens), fmt.tok(r.body_tokens),
                     str(r.invocations) if r.invocations else c("never", "grey"), str(r.sessions) if r.sessions else "",
                     fmt.usd(r.carried_usd) if r.carried_usd else "", str(r.repeats) if r.repeats else ""])
    for r in rep.unlisted.values():
        rows.append([r.name[:30], r.source, c("?", "grey"), fmt.tok(r.body_tokens), str(r.invocations), str(r.sessions),
                     fmt.usd(r.carried_usd), str(r.repeats) if r.repeats else ""])
    print(fmt.table(rows, "llrrrrrr"))
    print()
    if rep.unused:
        by_source: dict = {}
        for r in rep.unused:
            by_source.setdefault(r.source, []).append(r.name)
        print("  Never invoked in the window: {} skills, {} tokens of listing ≈ {} / {}".format(
            len(rep.unused), fmt.tok(sum(r.desc_tokens for r in rep.unused)), c(fmt.usd(rep.unused_usd), "green", "bold"), window))
        for source, names in sorted(by_source.items(), key=lambda kv: -len(kv[1])):
            hint = disable_hint(source)
            print("    {:<14s} {}{}".format(source, ", ".join(names), c("  → " + hint, "grey") if hint else ""))
    big = [r for r in rep.rows if r.carried_usd >= 1 and r.body_tokens >= 4_000]
    if big:
        print("  Large bodies: " + "; ".join("{} ({} tokens, {})".format(r.name, fmt.tok(r.body_tokens), fmt.usd(r.carried_usd)) for r in big[:4]))
        print(c("    Move reference sections into files the skill reads on demand (progressive disclosure), or run the step "
                "that needs the skill in a subagent so the text stays out of the main context.", "grey"))
    if rep.repeats:
        print(c("  Repeat injections: `kvleak refine` proposes a one-line rule against invoking a loaded skill again.", "grey"))
    print(c("  Bundled skills ship with Claude Code and cannot be removed; everything else can. Descriptions are tokens ≈ chars/4; "
            "the listing cost assumes every session lists every skill.", "grey"))
    return 0


def cmd_probe(args) -> int:
    """Before/after the day kvleak's changes were applied."""
    from .probe import MIN_SESSIONS, contrast, first_applied, load_events, projected_saving_30d, snapshot, to_dict

    c = fmt.c
    events = load_events()
    at = args.at if args.at is not None else first_applied(events)
    if at is None:
        print("kvleak: nothing recorded yet. Run kvleak tune --apply / hooks --install / refine --apply, or pass "
              "--at 2026-10-21 to split your history by hand.", file=sys.stderr)
        return 1
    before_s = args.before if args.before is not None else 30 * 86400
    now = time.time()
    transcripts = load_all(_default_paths(args), args.format, at - before_s, args.project)
    miss = _profile(args).miss_rate
    before = snapshot(transcripts, at - before_s, at, miss, args.min_miss)
    after = snapshot(transcripts, at, now + 1, miss, args.min_miss)
    if args.json:
        print(json.dumps(to_dict(before, after, at, events), indent=2, sort_keys=True))
        return 0
    when = datetime.fromtimestamp(at, tz=timezone.utc).date()
    applied = ", ".join("{} {}".format(e.get("what", "?"), datetime.fromtimestamp(float(e["at"]), tz=timezone.utc).date())
                        for e in sorted(events, key=lambda e: e["at"])) or "manual split"
    print(c("\n  kvleak probe · split at {} ({})".format(when, applied), "bold"))
    print("  before: {} sessions, {} calls over {:.0f}d · after: {} sessions, {} calls over {:.1f}d".format(
        before.sessions, f"{before.calls:,}", before.days, after.sessions, f"{after.calls:,}", after.days))
    if after.sessions < MIN_SESSIONS:
        print(c("  Too early: {} interactive session(s) since; probe wants {}. Keep working and run it again.".format(
            after.sessions, MIN_SESSIONS), "yellow"))
        if not after.calls:
            return 0
    show = {"usd": fmt.usd, "pct": fmt.pct, "tok": fmt.tok, "num": lambda v: "{:.1f}".format(v)}
    rows = [["", "before", "after", "change"]]
    for r in contrast(before, after):
        d = r.delta
        if d is None:
            change = "–"
        else:
            change = "{:+.0%}".format(d)
            change = c(change, "green" if d <= -0.1 else "red" if d >= 0.1 else "grey")
        rows.append([r.label, show[r.kind](r.before), show[r.kind](r.after), change])
    print()
    print(fmt.table(rows, "lrrr"))
    print()
    proj = projected_saving_30d(before, after)
    if after.calls and before.calls:
        verb = "less" if proj >= 0 else "more"
        print("  {} Input spend per call {} → {}: at the after-side call rate that is {} {} per 30 days.".format(
            c("→", "green" if proj > 0 else "yellow", "bold"), fmt.usd(before.input_per_call), fmt.usd(after.input_per_call),
            c(fmt.usd(abs(proj)), "green" if proj > 0 else "red", "bold"), verb))
    print(c("  Interactive sessions only (no one-shot runs, no subagents); a session counts on the side its first call "
            "falls on. Before/after on your own work, not a controlled comparison: per-call and share rows cancel "
            "most of what changed in the work itself.", "grey"))
    return 0


def _record_applied(what: str) -> None:
    from .probe import record

    record(what)


def _bash_cap_saving(agg, cap_chars: int) -> float:
    """Carried cost of the part of every Bash result beyond `cap_chars` (which would go to a file instead)."""
    cap = cap_chars / 4.0
    saving = 0.0
    for _, b in agg.bash_blocks:
        if b.est > cap:
            saving += b.carry_usd * (1 - cap / b.est)
    return saving


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
    print("  → {}  (restart Claude Code; undo with kvleak tune --reset · kvleak probe in a week shows the difference)".format(settings))
    _record_applied("tune")
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


def cmd_keepalive(args) -> int:
    from .advisor import Profile
    from .keepalive import HOUR_WIDTH, budgets, by_hour, learned, pauses, return_curve

    c = fmt.c
    transcripts = load_all(_default_paths(args), args.format, args.since, args.project)
    bs = budgets(transcripts, price_for)
    if not bs:
        print("kvleak: no pause longer than the TTL on a prefix ≥ 10k tokens in this history", file=sys.stderr)
        return 1
    window = "since {}".format(datetime.fromtimestamp(args.since, tz=timezone.utc).date()) if args.since else "all history"
    for b in bs:
        ttl = "1h" if b.ttl == TTL_1H else "5m"
        price = price_for(b.model)
        print(c("\n  Keep-alive budget from your own pauses · {} TTL · {} · {}".format(ttl, b.model, window), "bold"))
        print("  {} pauses outlived the TTL, {} of them never came back (session ended).".format(
            b.pauses, b.never_returned))
        print("  One ping re-reads the prefix at {:g}×; an expiry rebuilds it at {:g}×, so pinging can pay for up to {} pings.".format(
            price.read_mult, price.write_mult(b.ttl), b.kmax))
        print()
        rows = [["ping up to", "came back by then", "vs. never pinging"]]
        best, rec = b.best.pings, b.recommended
        for r in return_curve(b):
            net = fmt.usd(r.saving_usd) if price.input_per_mtok else fmt.tok(r.saving) + " tok"
            mark = "  ← budget" if r.pings == rec and rec else "  ← optimum" if r.pings == best and rec and best != rec else ""
            rows.append([str(r.pings), fmt.pct(r.returned),
                         ("–" if r.pings == 0 else c(net, "green" if r.saving_usd > 0 else "red")) + mark])
        print(fmt.table(rows, "rrl"))
        print()
        if rec:
            if best != rec:
                print("  {} Up to {} pings was the optimum (≈{} vs. letting every pause expire); {} ping{} already get{} {} of it "
                      "for a fraction of the requests, so that is the budget. With cache-refresher: {}".format(
                          c("→", "green", "bold"), best, fmt.usd(b.best.saving_usd), rec, "s" if rec > 1 else "",
                          "" if rec > 1 else "s", fmt.pct(b.rows[rec].saving_usd / b.best.saving_usd),
                          c("/cache-auto on {}".format(rec), "bold")))
            else:
                print("  {} Pinging up to {} time{} paid on this history: ≈{} saved vs. letting every pause expire. "
                      "With cache-refresher: {}".format(
                          c("→", "green", "bold"), rec, "s" if rec > 1 else "",
                          fmt.usd(b.best.saving_usd), c("/cache-auto on {}".format(rec), "bold")))
        else:
            print("  {} Pinging never paid on this history: you rarely come back before the rebuild would have cost less. "
                  "Let it expire; reply before the countdown ends when you can.".format(c("→", "yellow", "bold")))
        hours = by_hour(pauses(transcripts), price_for, b.ttl)
        if len(hours) >= 2:
            print()
            print("  By local time of day the pause began (the statusline uses this):")
            rows = [["pause began", "pauses", "never came back", "budget", "saved"]]
            for start, hb in hours:
                k = hb.recommended
                rows.append(["{:02d}–{:02d}h".format(start, start + HOUR_WIDTH - 1), str(hb.pauses),
                             fmt.pct(hb.never_returned / hb.pauses),
                             c("{} ping{}".format(k, "s" if k > 1 else ""), "green") if k else c("let it expire", "yellow"),
                             fmt.usd(hb.rows[k].saving_usd) if k else "–"])
            print(fmt.table(rows, "lrrrr"))
    if not args.path and not args.project and not args.since:
        prof = Profile.load()
        prof.keepalive, prof.keepalive_hours = learned(transcripts, price_for)
        prof.save()
    print(c("\n  Money only: a ping is also one request of quota on a subscription. The refresh must come from inside "
            "Claude Code (same prefix); kvleak only sizes the budget.", "grey"))
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


def _kvleak_command() -> str:
    """How settings.json should invoke kvleak."""
    exe = shutil.which("kvleak")
    if exe:
        return exe
    if shutil.which("uvx"):  # running from an ephemeral `uvx kvleak` env; don't pin its path
        return "uvx kvleak"
    return "{} -m kvleak".format(sys.executable)


def cmd_hook(args) -> int:
    from .hooks import run_hook

    return run_hook(args.event, args.max_chars)


def cmd_hooks(args) -> int:
    from .hooks import cache_root, install, installed, uninstall

    data = _read_settings(args.project)
    if data is None:
        return 1
    if args.uninstall:
        if not installed(data):
            print("kvleak hooks are not installed")
            return 0
        settings = _write_settings(uninstall(data), args.project)
        print("Removed kvleak hooks from {}".format(settings))
        return 0
    if not args.install:
        have = installed(data)
        print("kvleak hooks: {}".format(", ".join(have) if have else "not installed") + "\n"
              "  archive: {}\n  install: kvleak hooks --install [--max-chars 8000] [--project]".format(cache_root()))
        return 0
    settings = _write_settings(install(data, _kvleak_command(), args.max_chars), args.project)
    _record_applied("hooks")
    print("Installed in {}:\n"
          "  PostToolUse (Bash)      results over {:,} chars go to {}/outputs/<session>/ and the model sees head + tail +\n"
          "                          error lines + the path, instead of all of it\n"
          "  PreCompact / PostCompact every block the compaction drops is archived under {}/history/<session>/\n"
          "  SessionStart (compact)  the model is told the archive exists and how to `kvleak recall` from it\n"
          "  Takes effect in new sessions. Undo: kvleak hooks --uninstall".format(
              settings, args.max_chars, cache_root(), cache_root()))
    return 0


def cmd_recall(args) -> int:
    from .hooks import archived_sessions, recall, resolve_session

    sid = resolve_session(args.session)
    if not sid:
        if args.session:
            print("kvleak: no archived history for a session starting with {!r}".format(args.session), file=sys.stderr)
        else:
            print("kvleak: nothing archived yet. Install the hooks (kvleak hooks --install); the first compaction fills the archive.",
                  file=sys.stderr)
        return 1
    if args.list:
        for p in archived_sessions()[:20]:
            print("  {}  {}".format(p.name[:8], datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M")))
        return 0
    print(recall(sid, args.query, args.show, limit=args.limit, context=args.context))
    return 0


def cmd_refine(args) -> int:
    from .diet import aggregate
    from .refine import (
        HEADING,
        METRIC_LABEL,
        apply,
        load_state,
        metrics,
        probe,
        propose,
        remove,
        render,
        save_state,
    )

    c = fmt.c
    target = Path(args.file) if args.file else (Path.cwd() / "CLAUDE.md" if args.local else _claude_dir() / "CLAUDE.md")
    if args.remove:
        print("Removed the kvleak block from {}".format(target) if remove(target) else "No kvleak block in {}".format(target))
        return 0
    miss = _profile(args).miss_rate
    if args.probe:
        state = load_state()
        if not state:
            print("kvleak: no rules applied yet (kvleak refine --apply)", file=sys.stderr)
            return 1
        after_t = load_all(_default_paths(args), args.format, state["applied_at"], args.project)
        agg = aggregate(after_t, miss)
        if agg.sessions < 3:
            print("kvleak: only {} interactive session(s) since the rules were applied on {}; probe needs 3.".format(
                agg.sessions, datetime.fromtimestamp(state["applied_at"]).strftime("%Y-%m-%d")))
            return 0
        print(c("\n  Rules applied {} · {} sessions before ({}) vs {} sessions since".format(
            datetime.fromtimestamp(state["applied_at"]).strftime("%Y-%m-%d"), state.get("baseline_sessions", "?"),
            state.get("window", "?"), agg.sessions), "bold"))
        rows = [["rule", "metric", "before", "after", "change", "verdict"]]
        for ct in probe(state, metrics(agg)):
            rows.append([ct.rule, METRIC_LABEL.get(ct.metric, ct.metric), _metric_fmt(ct.metric, ct.before),
                         _metric_fmt(ct.metric, ct.after), "{:+.0%}".format(ct.change),
                         c(ct.verdict, "green" if ct.verdict.startswith("kept") else "yellow" if "worse" in ct.verdict else "grey")])
        print(fmt.table(rows, "llrrrl"))
        print(c("  Behavioural contrast only: it shows whether the numbers moved, not that the rules caused it.", "grey"))
        return 0
    since = args.since if args.since is not None else time.time() - 30 * 86400
    transcripts = load_all(_default_paths(args), args.format, since, args.project)
    agg = aggregate(transcripts, miss)
    if not agg.kinds:
        print("kvleak: no interactive sessions in that window", file=sys.stderr)
        return 1
    rules = propose(agg, _bash_cap_saving(agg, 8_000), args.min_usd)
    window = _window_label(since)
    if not rules:
        print("No rule clears ${:.0f} of evidence in the last {}. Nothing to add.".format(args.min_usd, window))
        return 0
    block = render(rules, window)
    print(c("\n  {} rules with evidence from {} sessions · {}".format(len(rules), agg.sessions, window), "bold"))
    for r in rules:
        print("  {} {}  {}".format(c("•", "green"), c(r.id, "bold"), c("~" + fmt.usd(r.saving_usd) + " / " + window, "green")))
        print("    {}".format(r.text))
        print(c("    evidence: {}".format(r.evidence), "grey"))
    if not args.apply:
        print(c("\n  Write them under '{}' in {}: kvleak refine --apply   (project file: --local)".format(HEADING, target), "grey"))
        return 0
    apply(target, block)
    _record_applied("refine")
    base = metrics(agg)
    save_state(rules, base, target, window)
    st = load_state() or {}
    st["baseline_sessions"] = agg.sessions
    from .refine import state_path

    state_path().write_text(json.dumps(st, indent=1), encoding="utf-8")
    print("\nWrote {} rules to {} (backup: {}.kvleak.bak). They cost about {} tokens per call to carry.\n"
          "  In a week: kvleak refine --probe   · undo: kvleak refine --remove".format(
              len(rules), target, target, fmt.tok(len(block) / 4)))
    return 0


def _metric_fmt(metric: str, v: float) -> str:
    if metric.endswith("share"):
        return fmt.pct(v)
    if metric.endswith("usd_per_call"):
        return fmt.usd(v)
    return "{:,.1f}".format(v)


def _claude_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude"))


def cmd_statusline(args) -> int:
    if not args.install:
        from .statusline import main as sl_main

        return sl_main()
    data = _read_settings()
    if data is None:
        return 1
    command = "{} statusline".format(_kvleak_command())
    existing = data.get("statusLine")
    if existing and "kvleak" not in json.dumps(existing) and not args.force:
        print("kvleak: you already have a statusLine:\n  {}\nRe-run with --force to replace it, or add\n  {}\n"
              "to your own statusline script.".format(json.dumps(existing), command))
        return 1
    line = {"type": "command", "command": command, "padding": 0}
    # Claude Code re-runs the statusline on events only (a message, a tool call, the cache going cold), so
    # without a timer the countdown freezes while you read or think. refreshInterval re-runs it every N seconds.
    if args.refresh > 0:
        line["refreshInterval"] = args.refresh
    data["statusLine"] = line
    settings = _write_settings(data)
    tick = ("ticks every {}s while idle (--refresh N to change, 0 for events only)".format(args.refresh)
            if args.refresh > 0 else "updates on events only (--refresh N to tick while idle)")
    print("Installed. New Claude Code sessions show the cache countdown; it {}.\n"
          "  settings: {}\n  undo:     kvleak statusline --uninstall".format(tick, settings))
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

    di = sub.add_parser("diet", help="what filled the context and what each piece cost to carry (newest session)")
    _add_common(di)
    di.add_argument("id", nargs="?", help="session id (prefix ok) or transcript path")
    di.add_argument("--all", action="store_true", help="aggregate every interactive session in the window (30d default)")
    di.add_argument("--top", type=int, default=8, help="single items to list (default 8)")
    di.set_defaults(func=cmd_diet)

    hk = sub.add_parser("hooks", help="install Claude Code hooks: condense long shell output, archive what compaction drops")
    hk.add_argument("--install", action="store_true", help="add the hooks to ~/.claude/settings.json")
    hk.add_argument("--uninstall", action="store_true", help="remove them again")
    hk.add_argument("--project", action="store_true", help="use ./.claude/settings.json instead")
    hk.add_argument("--max-chars", type=int, default=8_000, metavar="N",
                    help="Bash results longer than this are archived and condensed (default 8000)")
    hk.add_argument("--no-color", action="store_true")
    hk.set_defaults(func=cmd_hooks)

    h = sub.add_parser("hook", help="hook entry point used by settings.json (reads the hook payload on stdin)")
    h.add_argument("event", choices=["post-tool", "pre-compact", "post-compact", "session-start"])
    h.add_argument("--max-chars", type=int, default=8_000)
    h.set_defaults(func=cmd_hook)

    rc = sub.add_parser("recall", help="search the archived history of a compacted session")
    rc.add_argument("query", nargs="?", help="regex; without it, the map of archived requests")
    rc.add_argument("--session", help="session id prefix (default: most recently archived)")
    rc.add_argument("--show", type=int, metavar="N", help="print record N in full")
    rc.add_argument("--list", action="store_true", help="archived sessions")
    rc.add_argument("--limit", type=int, default=40, help="matching records to show (default 40)")
    rc.add_argument("--context", type=int, default=1, help="lines around each match (default 1)")
    rc.add_argument("--no-color", action="store_true")
    rc.set_defaults(func=cmd_recall)

    rf = sub.add_parser("refine", help="evidence-backed context-hygiene rules for CLAUDE.md, from your history (30d)")
    _add_common(rf)
    rf.add_argument("--apply", action="store_true", help="write the rules to ~/.claude/CLAUDE.md (backed up)")
    rf.add_argument("--local", action="store_true", help="write to ./CLAUDE.md instead of ~/.claude/CLAUDE.md")
    rf.add_argument("--file", help="write to this file instead")
    rf.add_argument("--remove", action="store_true", help="delete the kvleak block again")
    rf.add_argument("--probe", action="store_true", help="contrast sessions since --apply with the ones before")
    rf.add_argument("--min-usd", type=float, default=5.0, metavar="USD",
                    help="only propose a rule whose evidence is worth at least this much (default 5)")
    rf.set_defaults(func=cmd_refine)

    wi = sub.add_parser("whatif", help="replay other tools' promises (rtk, caveman, …) on your own bill")
    _add_common(wi)
    wi.add_argument("--min-window", type=parse_tokens, default=150_000, metavar="TOKENS", help=argparse.SUPPRESS)
    wi.set_defaults(func=cmd_whatif)

    pr = sub.add_parser("probe", help="before/after the day kvleak's changes went in: did it save anything?")
    _add_common(pr)
    pr.add_argument("--at", type=parse_since, metavar="DATE", help="split here instead of the recorded apply date (2026-10-21 or 7d)")
    pr.add_argument("--before", type=lambda v: time.time() - parse_since(v), metavar="WINDOW",
                    help="how much history before the split to compare against (default 30d)")
    pr.add_argument("--json", action="store_true")
    pr.set_defaults(func=cmd_probe)

    sk = sub.add_parser("skills", help="what the skill listing costs on every call, and what loaded skill bodies cost to carry")
    _add_common(sk)
    sk.add_argument("--json", action="store_true")
    sk.set_defaults(func=cmd_skills)

    ka = sub.add_parser("keepalive", help="how many keep-alive pings your own idle pattern justifies")
    _add_common(ka)
    ka.set_defaults(func=cmd_keepalive)

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
    sl.add_argument("--refresh", type=int, default=5, metavar="N",
                    help="also re-run every N seconds so the countdown ticks while idle (default 5; 0 = events only)")
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
