"""Continual harness: turn what the audit measured into rules the agent reads.

Harness-improvement loops (Harness², Prime Agent's `/refine`) edit the files
around a model (system prompt notes, memory, skills) and keep only edits that
evidence supports. Claude Code's always-on file is CLAUDE.md. `kvleak refine`
proposes a short block of context-hygiene rules for it, each gated on a number
from your own history, writes it on `--apply`, and on `--probe` contrasts the
sessions since then with the ones before to tell which rules changed behaviour.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from .diet import SKILL_KIND, TEXT_KIND, Aggregate

BEGIN = "<!-- kvleak:begin"
END = "<!-- kvleak:end -->"
HEADING = "## Context hygiene (measured by kvleak on this machine)"


RULE_TEXT = {
    "shell-output": "Shell output: when a command can print more than ~100 lines (tests, builds, logs, listings), redirect "
                    "it to a file and read the part you need with `tail -n 60`, `grep` or `sed -n`; never let it come back whole.",
    "scripts-in-files": "Scripts: write any multi-line script or heredoc to a file in the scratchpad once and run the file; "
                        "iterate with Edit on that file. Every inline script is re-sent on every later call.",
    "loops-in-subagents": "Long loops: when a task will take more than ~20 tool calls (test-fix cycles, bulk edits, data runs), "
                          "run it in a subagent that reports back a few lines; do not run it in the main conversation.",
    "batch-steps": "Mechanical steps: combine sequences of `ls`, `cat`, `grep`, `find` and small reads into one command "
                   "or one script. Each separate call re-reads the whole context; one script reads it once.",
    "edit-not-write": "Files: change existing files with Edit, not by rewriting them with Write; generate large or "
                      "repetitive files with a script instead of writing them out literally.",
    "terse-in-loops": "Narration: inside tool loops keep status to one line and put reports in files; everything you say "
                      "is re-read on every later call.",
    "skills-once": "Skills: invoke a skill once per session; if its instructions are already in the context, do not call "
                   "the Skill tool for it again. When only one step needs a large skill, run that step in a subagent so "
                   "the skill text stays out of the main conversation.",
}


@dataclass
class Rule:
    id: str
    text: str  # the instruction, one or two sentences
    evidence: str  # why, from the history
    saving_usd: float
    metric: str  # the probe metric this rule should move (lower is better)


def metrics(agg: Aggregate) -> Dict[str, float]:
    """Behaviour numbers a rule can be judged on. All are per call or shares; lower is better."""
    total = sum(v["tokens"] for v in agg.kinds.values()) or 1.0
    calls = max(agg.calls, 1)
    bash = agg.kinds.get("Bash", {"tokens": 0.0, "count": 0})
    bash_in = agg.kinds.get("Bash inputs", {"tokens": 0.0})
    write_in = agg.kinds.get("Write inputs", {"tokens": 0.0})
    text = agg.kinds.get(TEXT_KIND, {"tokens": 0.0})
    skill = agg.kinds.get(SKILL_KIND, {"tokens": 0.0})
    return {
        "bash_result_tokens_per_result": bash["tokens"] / max(bash["count"], 1),
        "bash_input_share": bash_in["tokens"] / total,
        "write_input_share": write_in["tokens"] / total,
        "text_share": text["tokens"] / total,
        "skill_body_share": skill["tokens"] / total,
        "long_turn_call_share": agg.long_turn_calls / calls,
        "chain_call_share": agg.chain_calls / calls,
        "calls_per_turn": calls / max(agg.turns, 1),
        "carry_usd_per_call": agg.carry_usd / calls,
    }


METRIC_LABEL = {
    "bash_result_tokens_per_result": "tokens per Bash result",
    "bash_input_share": "share of context that is scripts sent to Bash",
    "write_input_share": "share of context that is Written files",
    "text_share": "share of context that is reply text",
    "skill_body_share": "share of context that is skill bodies",
    "long_turn_call_share": "share of calls inside 20+ call loops",
    "chain_call_share": "share of calls inside mechanical chains",
    "calls_per_turn": "calls per request",
    "carry_usd_per_call": "carried cost per call ($)",
}


def propose(agg: Aggregate, bash_cap_saving: float, min_usd: float = 5.0) -> List[Rule]:
    """Rules whose evidence clears `min_usd` over the window, biggest first."""
    total = sum(v["tokens"] for v in agg.kinds.values()) or 1.0
    rules: List[Rule] = []
    bash = agg.kinds.get("Bash")
    if bash and bash_cap_saving >= min_usd:
        big = sum(1 for _, b in agg.bash_blocks if b.est > 2_000)
        rules.append(Rule(
            "shell-output", RULE_TEXT["shell-output"],
            "{} Bash results over 8k chars; carrying the part beyond 8k cost {}".format(big, _usd(bash_cap_saving)),
            bash_cap_saving, "bash_result_tokens_per_result"))
    bash_in = agg.kinds.get("Bash inputs")
    if bash_in and bash_in["tokens"] / total >= 0.12 and bash_in["carry_usd"] * 0.5 >= min_usd:
        rules.append(Rule(
            "scripts-in-files", RULE_TEXT["scripts-in-files"],
            "scripts sent inline to Bash were {:.0%} of the context, carried for {}".format(
                bash_in["tokens"] / total, _usd(bash_in["carry_usd"])),
            bash_in["carry_usd"] * 0.5, "bash_input_share"))
    if agg.subagent_saving_usd >= min_usd:
        rules.append(Rule(
            "loops-in-subagents", RULE_TEXT["loops-in-subagents"],
            "{} loops of 20+ calls re-read {} calls of history for {}; in subagents that would have been {} less".format(
                agg.long_turns, f"{agg.long_turn_calls:,}", _usd(agg.history_usd), _usd(agg.subagent_saving_usd)),
            agg.subagent_saving_usd, "long_turn_call_share"))
    if agg.chain_saving_usd >= min_usd:
        rules.append(Rule(
            "batch-steps", RULE_TEXT["batch-steps"],
            "{} chains of 3+ mechanical calls ({} calls); as single scripts they would have saved up to {}".format(
                agg.chains, f"{agg.chain_calls:,}", _usd(agg.chain_saving_usd)),
            agg.chain_saving_usd, "chain_call_share"))
    write_in = agg.kinds.get("Write inputs")
    if write_in and write_in["tokens"] / total >= 0.04 and write_in["carry_usd"] * 0.5 >= min_usd:
        rules.append(Rule(
            "edit-not-write", RULE_TEXT["edit-not-write"],
            "Written files were {:.0%} of the context (largest {:,} tokens), carried for {}".format(
                write_in["tokens"] / total, int(write_in["max"]), _usd(write_in["carry_usd"])),
            write_in["carry_usd"] * 0.5, "write_input_share"))
    text = agg.kinds.get(TEXT_KIND)
    if text and text["tokens"] / total >= 0.08 and text["carry_usd"] * 0.5 >= min_usd:
        rules.append(Rule(
            "terse-in-loops", RULE_TEXT["terse-in-loops"],
            "reply text was {:.0%} of the context, carried for {}".format(text["tokens"] / total, _usd(text["carry_usd"])),
            text["carry_usd"] * 0.5, "text_share"))
    if agg.skill_repeat_usd >= min_usd:
        rules.append(Rule(
            "skills-once", RULE_TEXT["skills-once"],
            "{} repeat invocations of a skill already in the context, carried for {}".format(
                agg.skill_repeats, _usd(agg.skill_repeat_usd)),
            agg.skill_repeat_usd, "skill_body_share"))
    return sorted(rules, key=lambda r: -r.saving_usd)


def _usd(v: float) -> str:
    return "${:,.0f}".format(v) if v >= 10 else "${:.2f}".format(v)


def render(rules: List[Rule], window: str) -> str:
    today = time.strftime("%Y-%m-%d")
    lines = ["{} {} · {} · remove with `kvleak refine --remove` -->".format(BEGIN, today, window), HEADING, ""]
    for r in rules:
        lines.append("- {} _(kvleak: {})_".format(r.text, r.evidence))
    lines += ["", END, ""]
    return "\n".join(lines)


def block_re() -> "re.Pattern[str]":
    return re.compile(re.escape(BEGIN) + r".*?" + re.escape(END) + r"\n?", re.DOTALL)


def apply(path: Path, block: str) -> Path:
    """Replace the kvleak block in CLAUDE.md, or append it; backs the file up first."""
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    if path.exists():
        shutil.copy(path, str(path) + ".kvleak.bak")
    if block_re().search(old):
        new = block_re().sub(lambda _m: block, old, count=1)
    else:
        new = old.rstrip("\n") + ("\n\n" if old.strip() else "") + block
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new, encoding="utf-8")
    return path


def remove(path: Path) -> bool:
    if not path.exists():
        return False
    old = path.read_text(encoding="utf-8")
    new, n = block_re().subn("", old)
    if n:
        path.write_text(new.rstrip("\n") + "\n" if new.strip() else "", encoding="utf-8")
    return bool(n)


def state_path() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "kvleak" / "refine.json"


def save_state(rules: List[Rule], baseline: Dict[str, float], path: Path, window: str) -> None:
    state_path().parent.mkdir(parents=True, exist_ok=True)
    state_path().write_text(json.dumps({
        "applied_at": time.time(), "path": str(path), "window": window,
        "rules": [{"id": r.id, "metric": r.metric} for r in rules], "baseline": baseline,
    }, indent=1), encoding="utf-8")


def load_state() -> Optional[dict]:
    try:
        return json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


@dataclass
class Contrast:
    rule: str
    metric: str
    before: float
    after: float

    @property
    def change(self) -> float:
        return (self.after - self.before) / self.before if self.before else 0.0

    @property
    def verdict(self) -> str:
        if self.change <= -0.10:
            return "kept: moved the right way"
        if self.change >= 0.10:
            return "got worse: consider removing"
        return "no clear change yet"


def probe(state: dict, after: Dict[str, float]) -> List[Contrast]:
    """Harness²'s probe step, on real sessions: the same metrics before and after the rules were applied."""
    before = state.get("baseline") or {}
    return [Contrast(r["id"], r["metric"], float(before.get(r["metric"], 0.0)), float(after.get(r["metric"], 0.0)))
            for r in state.get("rules") or []]
