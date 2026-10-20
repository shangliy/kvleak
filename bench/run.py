#!/usr/bin/env python3
"""Before/after benchmark: Claude Code with and without kvleak's hooks and rules.

    python bench/run.py --selftest                 # graders sane? (no model calls)
    python bench/run.py --arms baseline,kvleak     # 6 tasks × 2 arms, 1 repeat
    python bench/run.py --arms baseline,hooks,rules,kvleak --repeats 2 --concurrency 3
    python bench/run.py --report bench/results/<dir>   # re-render the report

Arms are feature sets: `baseline` = nothing; `hooks` = kvleak's four hooks
passed with `--settings`; `rules` = the five CLAUDE.md rules `kvleak refine`
proposed on the maintainer's history; `kvleak` = both. Every arm runs the same
`claude -p` with the same model, effort, turn cap and budget cap, in its own
fresh copy of the task repo. Accuracy is the task's hidden grader; cost is
Claude Code's own `total_cost_usd` estimate plus kvleak's list-price pricing
of the transcript (calls, context carried, cache reads and writes).
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, List, Optional

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(HERE))

import tasks as T  # noqa: E402

from kvleak.diet import read_diet  # noqa: E402
from kvleak.hooks import hook_entries  # noqa: E402
from kvleak.pricing import price_for  # noqa: E402
from kvleak.refine import RULE_TEXT, Rule, render  # noqa: E402
from kvleak.sources import default_claude_root, read_claude  # noqa: E402

ARMS = {"baseline": set(), "hooks": {"hooks"}, "rules": {"rules"}, "kvleak": {"hooks", "rules"}}

# The rules `kvleak refine` proposed on the maintainer's 30 days (docs/benchmarks.md §7), evidence included,
# because the evidence is part of the instruction the model reads.
MAINTAINER_RULES = [
    Rule("loops-in-subagents", RULE_TEXT["loops-in-subagents"],
         "116 loops of 20+ calls re-read 4,754 calls of history for $353; in subagents that would have been $320 less", 320, ""),
    Rule("batch-steps", RULE_TEXT["batch-steps"],
         "493 chains of 3+ mechanical calls (2,235 calls); as single scripts they would have saved up to $160", 160, ""),
    Rule("scripts-in-files", RULE_TEXT["scripts-in-files"],
         "scripts sent inline to Bash were 21% of the context, carried for $206", 103, ""),
    Rule("shell-output", RULE_TEXT["shell-output"], "270 Bash results over 8k chars; carrying the part beyond 8k cost $32", 32, ""),
    Rule("edit-not-write", RULE_TEXT["edit-not-write"],
         "Written files were 5% of the context (largest 27,094 tokens), carried for $47", 24, ""),
]


def kvleak_exe() -> str:
    for cand in (ROOT / ".venv" / "bin" / "kvleak", Path(shutil.which("kvleak") or "")):
        if cand and cand.exists():
            return str(cand)
    return "{} -m kvleak".format(sys.executable)


def transcript_for(session_id: str) -> Optional[str]:
    root = default_claude_root()
    hits = glob.glob(str(root / "*" / (session_id + ".jsonl")))
    return hits[0] if hits else None


def transcript_metrics(path: str) -> Dict:
    t = read_claude(path)
    price = price_for(t.steps[-1].model if t.steps else None)
    inp = sum(s.input for s in t.steps)
    w5, w1h, rd, out = (sum(getattr(s, k) for s in t.steps) for k in ("write_5m", "write_1h", "read", "output"))
    base_eq = inp + w5 * price.write_5m_mult + w1h * price.write_1h_mult + rd * price.read_mult
    d = read_diet(path)
    kinds = d.by_kind()
    bash = kinds.get("Bash", {"tokens": 0.0, "count": 0})
    return {
        "calls": len(t.steps),
        "peak_context": max((s.prompt for s in t.steps), default=0),
        "context_sum": sum(s.prompt for s in t.steps),  # tokens that "showed up" across all calls
        "uncached": inp, "write_5m": w5, "write_1h": w1h, "read": rd, "output": out,
        "input_usd_list": price.usd(base_eq), "output_usd_list": price.usd(out * price.output_mult),
        "bash_result_tokens": bash["tokens"], "bash_results": bash["count"],
        "tokens_entered": d.total_tokens, "chains": len(d.chains), "chain_calls": sum(c.calls for c in d.chains),
        "long_turns": len(d.long_turns), "compactions": d.compactions, "model": t.steps[-1].model if t.steps else None,
    }


def run_one(task_name: str, seed: int, arm: str, args, out_root: Path) -> Dict:
    feats = ARMS[arm]
    run_dir = out_root / "{}-s{}-{}".format(task_name, seed, arm)
    repo, hidden = run_dir / "repo", run_dir / "hidden"
    if run_dir.exists():
        shutil.rmtree(run_dir)
    repo.mkdir(parents=True)
    hidden.mkdir()
    task = T.make(task_name, seed)
    task.setup(repo, hidden)
    subprocess.run(["git", "init", "-q"], cwd=str(repo))
    subprocess.run(["git", "add", "-A"], cwd=str(repo))
    subprocess.run(["git", "-c", "user.email=bench@kvleak", "-c", "user.name=bench", "commit", "-q", "-m", "task"], cwd=str(repo))
    env = dict(os.environ)
    env.pop("CLAUDECODE", None)  # allow a nested run from inside a Claude Code session
    env["XDG_CACHE_HOME"] = str(run_dir / "cache")
    cmd = ["claude", "-p", task.prompt, "--output-format", "json", "--max-turns", str(args.max_turns),
           "--max-budget-usd", str(args.max_budget_usd), "--permission-mode", "bypassPermissions"]
    if args.model:
        cmd += ["--model", args.model]
    if args.effort:
        cmd += ["--effort", args.effort]
    if "hooks" in feats:
        settings = run_dir / "settings.json"
        settings.write_text(json.dumps({"hooks": hook_entries(kvleak_exe(), args.max_chars)}, indent=1))
        cmd += ["--settings", str(settings)]
    if "rules" in feats:
        (repo / "CLAUDE.md").write_text(render(MAINTAINER_RULES, "30d"))
    t0 = time.time()
    try:
        proc = subprocess.run(cmd, cwd=str(repo), capture_output=True, text=True, timeout=args.timeout, env=env)
        raw, err, rc = proc.stdout, proc.stderr, proc.returncode
    except subprocess.TimeoutExpired as exc:
        raw, err, rc = exc.stdout or "", (exc.stderr or "") + "\n[timeout]", -1
    wall = time.time() - t0
    (run_dir / "claude.json").write_text(raw if isinstance(raw, str) else raw.decode("utf-8", "replace"))
    (run_dir / "stderr.txt").write_text(err if isinstance(err, str) else err.decode("utf-8", "replace"))
    try:
        cc = json.loads(raw)
    except ValueError:
        cc = {}
    try:
        grade = task.grade(repo, hidden)
    except Exception as exc:  # grader must never take the run down
        grade = {"score": 0.0, "grader_error": repr(exc)}
    rec = {"task": task_name, "seed": seed, "arm": arm, "features": sorted(feats), "rc": rc, "wall_s": round(wall, 1),
           "session_id": cc.get("session_id"), "cc_cost_usd": cc.get("total_cost_usd"), "num_turns": cc.get("num_turns"),
           "is_error": cc.get("is_error"), "cc_usage": cc.get("usage"), "grade": grade, "score": grade.get("score", 0.0)}
    tp = transcript_for(cc.get("session_id") or "")
    if tp:
        time.sleep(1)  # the transcript is written asynchronously
        try:
            rec["transcript"] = transcript_metrics(tp)
        except Exception as exc:
            rec["transcript_error"] = repr(exc)
    (run_dir / "metrics.json").write_text(json.dumps(rec, indent=1))
    print("  {:<15} s{} {:<9} score {:<5} cost ${:<6} turns {:<4} {:.0f}s".format(
        task_name, seed, arm, rec["score"], rec["cc_cost_usd"] or 0, rec["num_turns"] or 0, wall), flush=True)
    return rec


# ---------------------------------------------------------------------- report


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return statistics.mean(xs) if xs else 0.0


def report(out_root: Path) -> str:
    recs = [json.loads(p.read_text()) for p in sorted(out_root.glob("*/metrics.json"))]
    arms = sorted({r["arm"] for r in recs}, key=lambda a: list(ARMS).index(a) if a in ARMS else 99)
    lines = ["# kvleak benchmark: {}".format(out_root.name), "",
             "{} runs · tasks: {} · arms: {}".format(len(recs), ", ".join(sorted({r["task"] for r in recs})), ", ".join(arms)), "",
             "Model: {} · cost = Claude Code's `total_cost_usd` estimate (list prices; on a subscription this is quota, not dollars) · "
             "input $ = kvleak list-price pricing of the transcript".format(next((r.get("transcript", {}).get("model") for r in recs if r.get("transcript")), "?")), "",
             "## Per arm (means over all runs)", "",
             "| arm | runs | accuracy | cost (CC est.) | input $ (kvleak) | calls | context carried (tok, sum of prompts) | Bash result tokens | chains | wall s |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    base = None
    for arm in arms:
        rs = [r for r in recs if r["arm"] == arm]
        tr = [r.get("transcript") or {} for r in rs]
        row = {
            "acc": _mean([r["score"] for r in rs]), "cost": _mean([r["cc_cost_usd"] for r in rs]),
            "inp": _mean([t.get("input_usd_list") for t in tr]), "calls": _mean([t.get("calls") for t in tr]),
            "ctx": _mean([t.get("context_sum") for t in tr]), "bash": _mean([t.get("bash_result_tokens") for t in tr]),
            "chains": _mean([t.get("chains") for t in tr]), "wall": _mean([r["wall_s"] for r in rs]),
        }
        base = base or row

        def d(k, row=row, base=base, first=arm == arms[0]):
            if first or not base[k]:
                return ""
            return " ({:+.0%})".format((row[k] - base[k]) / base[k])

        lines.append("| {} | {} | {:.2f}{} | ${:.2f}{} | ${:.2f}{} | {:.1f}{} | {:,.0f}{} | {:,.0f}{} | {:.1f}{} | {:.0f}{} |".format(
            arm, len(rs), row["acc"], d("acc"), row["cost"], d("cost"), row["inp"], d("inp"), row["calls"], d("calls"),
            row["ctx"], d("ctx"), row["bash"], d("bash"), row["chains"], d("chains"), row["wall"], d("wall")))
    lines += ["", "## Per task", "", "| task | arm | runs | accuracy | cost | calls | context carried | Bash result tokens | notes |", "|---|---|---:|---:|---:|---:|---:|---:|---|"]
    for task in sorted({r["task"] for r in recs}):
        for arm in arms:
            rs = [r for r in recs if r["task"] == task and r["arm"] == arm]
            if not rs:
                continue
            tr = [r.get("transcript") or {} for r in rs]
            notes = "; ".join(sorted({k + "=" + str(v) for r in rs for k, v in r["grade"].items() if k != "score"}))[:120]
            lines.append("| {} | {} | {} | {:.2f} | ${:.2f} | {:.0f} | {:,.0f} | {:,.0f} | {} |".format(
                task, arm, len(rs), _mean([r["score"] for r in rs]), _mean([r["cc_cost_usd"] for r in rs]),
                _mean([t.get("calls") for t in tr]), _mean([t.get("context_sum") for t in tr]), _mean([t.get("bash_result_tokens") for t in tr]), notes))
    paired = []
    if len(arms) >= 2:
        for task in sorted({r["task"] for r in recs}):
            for seed in sorted({r["seed"] for r in recs if r["task"] == task}):
                a = next((r for r in recs if r["task"] == task and r["seed"] == seed and r["arm"] == arms[0]), None)
                b = next((r for r in recs if r["task"] == task and r["seed"] == seed and r["arm"] == arms[-1]), None)
                if a and b and a.get("cc_cost_usd") and b.get("cc_cost_usd"):
                    paired.append((task, seed, a, b))
        if paired:
            lines += ["", "## Paired: {} vs {} on the same task and seed".format(arms[0], arms[-1]), "",
                      "| task | seed | accuracy | cost | calls |", "|---|---:|---|---|---|"]
            for task, seed, a, b in paired:
                lines.append("| {} | {} | {:.2f} → {:.2f} | ${:.2f} → ${:.2f} ({:+.0%}) | {} → {} |".format(
                    task, seed, a["score"], b["score"], a["cc_cost_usd"], b["cc_cost_usd"], (b["cc_cost_usd"] - a["cc_cost_usd"]) / a["cc_cost_usd"],
                    (a.get("transcript") or {}).get("calls", "?"), (b.get("transcript") or {}).get("calls", "?")))
            wins = sum(1 for _, _, a, b in paired if b["cc_cost_usd"] < a["cc_cost_usd"])
            lines.append("")
            lines.append("{} cheaper in {} of {} pairs; mean accuracy {:.2f} → {:.2f}; mean cost ${:.2f} → ${:.2f}.".format(
                arms[-1], wins, len(paired), _mean([a["score"] for _, _, a, _ in paired]), _mean([b["score"] for _, _, _, b in paired]),
                _mean([a["cc_cost_usd"] for _, _, a, _ in paired]), _mean([b["cc_cost_usd"] for _, _, _, b in paired])))
    lines += ["", "Runs that hit the turn cap or budget cap: {}".format(
        ", ".join("{}-s{}-{}".format(r["task"], r["seed"], r["arm"]) for r in recs if r.get("is_error") or r["rc"] != 0) or "none"), ""]
    text = "\n".join(lines)
    (out_root / "report.md").write_text(text)
    (out_root / "results.json").write_text(json.dumps(recs, indent=1))
    return text


# -------------------------------------------------------------------- selftest


def selftest(names: List[str], seed: int) -> int:
    bad = 0
    for name in names:
        with tempfile.TemporaryDirectory() as tmp:
            repo, hidden = Path(tmp) / "repo", Path(tmp) / "hidden"
            repo.mkdir()
            hidden.mkdir()
            task = T.make(name, seed)
            task.setup(repo, hidden)
            before = task.grade(repo, hidden)
            task.solve(repo, hidden)
            after = task.grade(repo, hidden)
            ok = before["score"] < 1.0 and after["score"] == 1.0
            bad += not ok
            print("  {:<15} before {:<5} after {:<5} {}  {}".format(name, before["score"], after["score"], "ok" if ok else "BAD",
                                                                   "" if ok else json.dumps({"before": before, "after": after})))
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tasks", default="all", help="comma list of task names (default all)")
    ap.add_argument("--arms", default="baseline,kvleak", help="comma list from: " + ", ".join(ARMS))
    ap.add_argument("--repeats", type=int, default=1, help="seeds per task (default 1)")
    ap.add_argument("--seed", type=int, default=1, help="first seed (default 1)")
    ap.add_argument("--max-turns", type=int, default=80)
    ap.add_argument("--max-budget-usd", type=float, default=4.0)
    ap.add_argument("--max-chars", type=int, default=8000, help="hook threshold for the hooks arms")
    ap.add_argument("--timeout", type=int, default=1800, help="seconds per run")
    ap.add_argument("--concurrency", type=int, default=2)
    ap.add_argument("--model", default=None)
    ap.add_argument("--effort", default=None)
    ap.add_argument("--out", default=None, help="results dir (default bench/results/<timestamp>)")
    ap.add_argument("--selftest", action="store_true", help="check graders with the reference solutions; no model calls")
    ap.add_argument("--report", default=None, help="re-render the report for an existing results dir")
    args = ap.parse_args()
    names = list(T.TASKS) if args.tasks == "all" else args.tasks.split(",")
    if args.selftest:
        return selftest(names, args.seed)
    if args.report:
        print(report(Path(args.report).resolve()))
        return 0
    arms = args.arms.split(",")
    for a in arms:
        if a not in ARMS:
            sys.exit("unknown arm {!r}; choose from {}".format(a, ", ".join(ARMS)))
    out_root = (Path(args.out) if args.out else HERE / "results" / time.strftime("%Y%m%d-%H%M%S")).resolve()
    out_root.mkdir(parents=True, exist_ok=True)
    jobs = [(n, s, a) for s in range(args.seed, args.seed + args.repeats) for n in names for a in arms]
    print("{} runs → {}".format(len(jobs), out_root), flush=True)
    with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        list(ex.map(lambda j: run_one(j[0], j[1], j[2], args, out_root), jobs))
    print()
    print(report(out_root))
    return 0


if __name__ == "__main__":
    sys.exit(main())
