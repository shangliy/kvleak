"""Skills: what the listing costs on every call, and what loaded skill bodies cost to carry.

Claude Code puts the name and description of every available skill into the
system prompt of every session (user skills, synced skills, enabled plugins,
bundled ones). That listing is part of the cached prefix: written once per
session start, read on every call. A skill nobody invokes still rides along.

When a skill is invoked its SKILL.md is injected as a (meta) user message and
stays in the context until the next compaction, re-read on every later call.
Invoking the same skill twice in one session injects it twice.

Both are measured here from the files on disk and the transcripts.
"""

from __future__ import annotations

import glob
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from .pricing import Price, price_for
from .sources import Transcript

CHARS_PER_TOKEN = 4.0
MARKER = "Base directory for this skill:"


@dataclass
class Installed:
    name: str
    source: str  # user, project, synced, plugin:<name>, bundled
    path: str
    desc_tokens: float
    body_tokens: float


def claude_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude"))


def parse_frontmatter(text: str) -> Tuple[Optional[str], str]:
    """(name, description) from a SKILL.md YAML header; tolerant of multi-line and quoted values."""
    if not text.startswith("---"):
        return None, ""
    end = text.find("\n---", 3)
    head = text[3:end] if end > 0 else text[3:]
    fields: Dict[str, str] = {}
    key = None
    for line in head.splitlines():
        m = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if m and not line.startswith((" ", "\t")):
            key = m.group(1)
            fields[key] = m.group(2).strip()
        elif key and line.strip():
            fields[key] += " " + line.strip()
    desc = fields.get("description", "").strip().strip("\"'").lstrip(">|").strip()
    return fields.get("name") or None, desc


def _read(path: str) -> Optional[Installed]:
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    name, desc = parse_frontmatter(text)
    return Installed(name or os.path.basename(os.path.dirname(path)), "", path,
                     len(desc) / CHARS_PER_TOKEN, len(text) / CHARS_PER_TOKEN)


def _walk(root: Path, max_depth: int = 4) -> List[str]:
    out: List[str] = []
    if not root.is_dir():
        return out
    base_depth = len(root.resolve().parts)
    for dirpath, dirnames, filenames in os.walk(str(root), followlinks=True):
        if len(Path(dirpath).resolve().parts) - base_depth >= max_depth:
            dirnames[:] = []
        if "SKILL.md" in filenames:
            out.append(os.path.join(dirpath, "SKILL.md"))
    return sorted(out)


def bundled_paths() -> List[str]:
    """Claude Code's own skills, unpacked under the temp dir per version."""
    roots = {tempfile.gettempdir(), "/tmp"}
    found: List[str] = []
    for r in roots:
        found += glob.glob(os.path.join(r, "claude-*", "bundled-skills", "*", "*", "*", "SKILL.md"))
    return sorted(found)


def bundled_names() -> List[str]:
    """Bundled skill folders (their SKILL.md is not always unpacked; the body still shows up in transcripts)."""
    names = set()
    for r in {tempfile.gettempdir(), "/tmp"}:
        for d in glob.glob(os.path.join(r, "claude-*", "bundled-skills", "*", "*", "*")):
            if os.path.isdir(d):
                names.add(os.path.basename(d))
    return sorted(names)


def enabled_plugins(settings: dict) -> List[Tuple[str, str]]:
    """[(plugin, marketplace)] for every `enabledPlugins` entry that is true."""
    out = []
    for key, on in (settings.get("enabledPlugins") or {}).items():
        if on and "@" in key:
            plugin, market = key.split("@", 1)
            out.append((plugin, market))
    return out


def installed(cdir: Optional[Path] = None, cwd: Optional[Path] = None, settings: Optional[dict] = None) -> List[Installed]:
    """Every SKILL.md whose description Claude Code lists, with its source."""
    cdir = cdir or claude_dir()
    cwd = cwd or Path.cwd()
    if settings is None:
        try:
            settings = json.loads((cdir / "settings.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            settings = {}
    found: List[Tuple[str, str]] = []
    for p in _walk(cdir / "skills"):
        rel = os.path.relpath(p, str(cdir / "skills")).split(os.sep)
        found.append(("synced" if "synced" in rel[:-1] else "user", p))
    for p in _walk(cwd / ".claude" / "skills"):
        found.append(("project", p))
    for plugin, market in enabled_plugins(settings):
        for p in _walk(cdir / "plugins" / "cache" / market / plugin):
            found.append(("plugin:" + plugin, p))
    for p in bundled_paths():
        found.append(("bundled", p))
    out: List[Installed] = []
    seen = set()
    for source, p in found:
        s = _read(p)
        if s is None or s.name in seen:
            continue
        s.source = source
        seen.add(s.name)
        out.append(s)
    return out


@dataclass
class Use:
    skill: str
    tokens: float
    call: int  # the call the body entered with
    carried: int = 0  # later calls that re-read it
    carry_usd: float = 0.0
    entry_usd: float = 0.0


def read_uses(path: str, miss_rate: float = 0.02) -> Tuple[List[Use], Dict[str, int]]:
    """Skill bodies injected in one transcript (priced), and Skill tool invocations by name."""
    uses: List[Use] = []
    invoked: Dict[str, int] = {}
    pending: List[Use] = []
    call = 0
    compactions: List[int] = []
    models: List[Optional[str]] = []
    seen = set()
    try:
        raw_all = Path(path).read_bytes()
    except OSError:
        return uses, invoked
    if MARKER.encode() not in raw_all and b'"Skill"' not in raw_all:
        return uses, invoked
    for raw in raw_all.splitlines():
        if not raw:
            continue
        try:
            d = json.loads(raw)
        except ValueError:
            continue
        t = d.get("type")
        m = d.get("message") or {}
        content = m.get("content")
        if t == "system" and d.get("subtype") == "compact_boundary":
            compactions.append(call)
            continue
        if t == "assistant":
            for part in content if isinstance(content, list) else []:
                if isinstance(part, dict) and part.get("type") == "tool_use" and part.get("name") == "Skill":
                    name = (part.get("input") or {}).get("skill")
                    if isinstance(name, str):
                        invoked[name] = invoked.get(name, 0) + 1
            usage = m.get("usage")
            key = (m.get("id"), d.get("requestId"))
            if usage and key not in seen and m.get("model") != "<synthetic>":
                seen.add(key)
                for u in pending:
                    u.call = call
                uses.extend(pending)
                pending = []
                models.append(m.get("model"))
                call += 1
            continue
        if t != "user":
            continue
        if d.get("isCompactSummary"):
            compactions.append(call)
        if not d.get("isMeta"):
            continue
        parts = [content] if isinstance(content, str) else [p.get("text") or "" for p in content or [] if isinstance(p, dict)]
        for text in parts:
            if text.startswith(MARKER):
                first = text.splitlines()[0][len(MARKER):].strip()
                pending.append(Use(os.path.basename(first.rstrip("/")) or "?", len(text) / CHARS_PER_TOKEN, call))
    ends = sorted(set(compactions)) + [call]
    for u in uses:
        end = next(e for e in ends if e >= u.call + 1)
        u.carried = max(end - u.call - 1, 0)
        for i in range(u.call + 1, u.call + 1 + u.carried):
            pr = price_for(models[i] if i < len(models) else None)
            u.carry_usd += pr.usd(u.tokens * (pr.read_mult + miss_rate * (pr.write_1h_mult - pr.read_mult))) or 0.0
        pr = price_for(models[u.call] if u.call < len(models) else None)
        u.entry_usd = pr.usd(u.tokens * pr.write_1h_mult) or 0.0
    return uses, invoked


def listing_usd(tokens: float, transcripts: Iterable[Transcript]) -> float:
    """What `tokens` of always-listed text cost: written at each session's first call, read on every call."""
    total = 0.0
    for t in transcripts:
        for i, s in enumerate(t.steps):
            pr: Price = price_for(s.model)
            if pr.input_per_mtok is None:
                continue
            if i == 0:
                total += pr.usd(tokens * (pr.write_1h_mult if s.write_1h > s.write_5m else pr.write_5m_mult)) or 0.0
            else:
                total += pr.usd(tokens * pr.read_mult) or 0.0
    return total


@dataclass
class Row:
    name: str
    source: str
    desc_tokens: float
    body_tokens: float
    invocations: int = 0
    sessions: int = 0
    carried_usd: float = 0.0  # bodies: entry + carry
    repeats: int = 0  # invoked again in a session that already had it


@dataclass
class SkillReport:
    rows: List[Row]
    sessions: int = 0
    calls: int = 0
    listing_tokens: float = 0.0
    listing_usd: float = 0.0
    unused: List[Row] = field(default_factory=list)
    unused_usd: float = 0.0
    body_usd: float = 0.0
    repeat_usd: float = 0.0
    repeats: int = 0
    unlisted: Dict[str, Row] = field(default_factory=dict)  # used in transcripts but not found on disk

    def to_dict(self) -> dict:
        def row(r: Row) -> dict:
            return {"name": r.name, "source": r.source, "description_tokens": round(r.desc_tokens),
                    "body_tokens": round(r.body_tokens), "invocations": r.invocations, "sessions": r.sessions,
                    "carried_usd": round(r.carried_usd, 2), "repeats": r.repeats}
        return {"sessions": self.sessions, "calls": self.calls, "listing_tokens": round(self.listing_tokens),
                "listing_usd": round(self.listing_usd, 2), "unused_usd": round(self.unused_usd, 2),
                "body_usd": round(self.body_usd, 2), "repeat_usd": round(self.repeat_usd, 2), "repeats": self.repeats,
                "skills": [row(r) for r in self.rows], "unused": [r.name for r in self.unused],
                "unlisted": [row(r) for r in self.unlisted.values()]}


def report(transcripts: List[Transcript], skills: List[Installed], miss_rate: float = 0.02) -> SkillReport:
    """Listing cost over every transcript; body cost over the interactive ones (where skills get invoked)."""
    rows = {s.name: Row(s.name, s.source, s.desc_tokens, s.body_tokens) for s in skills}
    rep = SkillReport(list(rows.values()))
    rep.calls = sum(len(t.steps) for t in transcripts)
    rep.sessions = len(transcripts)
    rep.listing_tokens = sum(s.desc_tokens for s in skills)
    rep.listing_usd = listing_usd(rep.listing_tokens, transcripts)
    from .policy import split

    groups = split([t for t in transcripts if t.source == "claude"])
    bundled = set(bundled_names())
    for t in groups.get("main", []) + groups.get("subagent", []):  # one-shot runs do not invoke skills
        uses, invoked = read_uses(t.path, miss_rate)
        names_here: Dict[str, int] = {}
        for u in uses:
            r = rows.get(u.skill) or rep.unlisted.setdefault(
                u.skill, Row(u.skill, "bundled" if u.skill in bundled else "?", 0.0, u.tokens))
            r.invocations += 1
            r.carried_usd += u.carry_usd + u.entry_usd
            rep.body_usd += u.carry_usd + u.entry_usd
            if names_here.get(u.skill):
                r.repeats += 1
                rep.repeats += 1
                rep.repeat_usd += u.carry_usd + u.entry_usd
            names_here[u.skill] = names_here.get(u.skill, 0) + 1
        for name in names_here:
            (rows.get(name) or rep.unlisted[name]).sessions += 1
        for name, n in invoked.items():  # tool calls whose body we did not see (skill ran elsewhere)
            short = name.split(":")[-1]
            r = rows.get(name) or rows.get(short)
            if r and not names_here.get(r.name):
                r.invocations += n
                r.sessions += 1
    rep.unused = [r for r in rep.rows if r.invocations == 0]
    rep.unused_usd = listing_usd(sum(r.desc_tokens for r in rep.unused), transcripts)
    rep.rows.sort(key=lambda r: (-r.carried_usd, -r.invocations, -r.desc_tokens))
    return rep


DISABLE_HINT = {
    "user": "move the folder out of ~/.claude/skills (e.g. to ~/.claude/skills-disabled)",
    "project": "move it out of ./.claude/skills",
    "synced": "unsync it in claude.ai → Settings → Skills",
    "bundled": "ships with Claude Code; cannot be removed",
}


def disable_hint(source: str) -> str:
    if source.startswith("plugin:"):
        return 'set "{}@…": false under enabledPlugins in settings.json'.format(source[7:])
    return DISABLE_HINT.get(source, "")
