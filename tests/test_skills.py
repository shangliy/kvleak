import json

from conftest import claude_line

from kvleak import skills
from kvleak.cli import main
from kvleak.diet import SKILL_KIND, aggregate, read_diet
from kvleak.refine import propose
from kvleak.sources import load_many


def _skill(path, name, desc, body_lines=40):
    path.mkdir(parents=True, exist_ok=True)
    (path / "SKILL.md").write_text(
        "---\nname: {}\ndescription: {}\n  continued on a second line\n---\n# {}\n".format(name, desc, name)
        + "\n".join("instruction line {} for the model to follow".format(i) for i in range(body_lines)))


def test_frontmatter_and_installed(tmp_path, monkeypatch):
    cdir = tmp_path / "claude"
    _skill(cdir / "skills" / "alpha", "alpha", "Does alpha things.")
    _skill(cdir / "skills" / "synced" / "abc123" / "beta", "beta", "Does beta things.")
    _skill(cdir / "plugins" / "cache" / "market" / "plug" / "skills" / "gamma", "gamma", "Plugin skill.")
    _skill(cdir / "plugins" / "cache" / "market" / "off" / "skills" / "delta", "delta", "Disabled plugin skill.")
    (cdir / "settings.json").write_text(json.dumps({"enabledPlugins": {"plug@market": True, "off@market": False}}))
    proj = tmp_path / "proj"
    _skill(proj / ".claude" / "skills" / "local", "local", "Project skill.")
    monkeypatch.setattr(skills, "bundled_paths", lambda: [])
    name, desc = skills.parse_frontmatter((cdir / "skills" / "alpha" / "SKILL.md").read_text())
    assert name == "alpha" and desc == "Does alpha things. continued on a second line"
    found = {s.name: s.source for s in skills.installed(cdir, proj)}
    assert found == {"alpha": "user", "beta": "synced", "gamma": "plugin:plug", "local": "project"}
    assert skills.disable_hint("plugin:plug").startswith('set "plug@')


def _session_with_skill(write_jsonl, name="alpha", repeat=True):
    body = skills.MARKER + " /home/me/.claude/skills/{}\n\n".format(name) + ("rule " * 400)
    meta = {"type": "user", "isMeta": True, "message": {"role": "user", "content": [{"type": "text", "text": body}]}}
    lines = [claude_line(0, 0, w1h=20_000)]
    lines.append(meta)
    at = 10
    for i in range(1, 5):
        lines.append(claude_line(i, at, read=20_000 + 2_000 * i, w1h=2_000))
        at += 10
    if repeat:
        lines.append(meta)
    for i in range(5, 9):
        lines.append(claude_line(i, at, read=30_000 + 2_000 * i, w1h=2_000))
        at += 10
    return write_jsonl(lines)


def test_uses_report_and_diet_kind(write_jsonl, tmp_path, monkeypatch):
    p = _session_with_skill(write_jsonl)
    uses, invoked = skills.read_uses(str(p))
    assert [u.skill for u in uses] == ["alpha", "alpha"]
    assert uses[0].call == 1 and uses[0].carried == 7 and uses[1].carried == 3
    assert uses[0].carry_usd > uses[1].carry_usd > 0
    # diet sees the same blocks as a kind of their own, and the aggregate counts the repeat
    d = read_diet(str(p))
    assert [b.label for b in d.blocks if b.kind == SKILL_KIND] == ["alpha", "alpha"]
    ts = load_many(str(p), "claude")
    agg = aggregate(ts)
    assert agg.skill_repeats == 1 and agg.skill_repeat_usd > 0
    assert any(r.id == "skills-once" for r in propose(agg, 0.0, min_usd=0.0))
    # the report: listing priced over every call, alpha used, beta never
    cdir = tmp_path / "claude"
    _skill(cdir / "skills" / "alpha", "alpha", "Alpha.")
    _skill(cdir / "skills" / "beta", "beta", "Beta, never used.")
    monkeypatch.setattr(skills, "bundled_paths", lambda: [])
    rep = skills.report(ts, skills.installed(cdir, tmp_path))
    assert rep.listing_usd > 0 and rep.unused_usd > 0 and rep.unused_usd < rep.listing_usd
    row = {r.name: r for r in rep.rows}
    assert row["alpha"].invocations == 2 and row["alpha"].sessions == 1 and row["alpha"].repeats == 1
    assert [r.name for r in rep.unused] == ["beta"] and rep.repeats == 1


def test_skills_cli(write_jsonl, tmp_path, monkeypatch, capsys):
    _session_with_skill(write_jsonl, repeat=False)
    cdir = tmp_path / "claude"
    _skill(cdir / "skills" / "alpha", "alpha", "Alpha.")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cdir))
    monkeypatch.setattr(skills, "bundled_paths", lambda: [])
    root = str(tmp_path / "projects")
    assert main(["skills", "--path", root, "--no-color"]) == 0
    out = capsys.readouterr().out
    assert "1 skills listed" in out and "alpha" in out and "Listed in every call" in out
    assert main(["skills", "--path", root, "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["skills"][0]["name"] == "alpha" and data["skills"][0]["invocations"] == 1 and data["unused"] == []
