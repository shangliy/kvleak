import json

from conftest import T0, claude_line

from kvleak import load
from kvleak.advisor import Profile, advise
from kvleak.cli import main
from kvleak.policy import best_ttl, dead_writes, idle_compaction, pick_window, split, subagent_starts, window_sweep
from kvleak.pricing import TTL_1H, price_for
from kvleak.statusline import render


def _interactive(write_jsonl, gap=600, n=40, name="sess-1.jsonl", sid="sess-1"):
    """A session with 1h writes and a fixed gap between calls."""
    lines, ctx = [claude_line(0, 0, w1h=120_000, model="claude-opus-5-5", sid=sid)], 120_000
    for i in range(1, n):
        lines.append(claude_line(i, i * gap, read=ctx, w1h=4_000, model="claude-opus-5-5", sid=sid))
        ctx += 4_000
    return load(str(write_jsonl(lines, name=name)), "claude")


def _headless(write_jsonl, n=30, spacing=20):
    out = []
    for i in range(n):
        line = claude_line(i, i * spacing, read=14_000, w1h=10_000, out=50, model="claude-opus-5-5", sid=f"run-{i}")
        out.append(load(str(write_jsonl([line], name=f"run-{i}.jsonl")), "claude"))
    return out


def test_split_by_workload(write_jsonl):
    t = _interactive(write_jsonl)
    runs = _headless(write_jsonl, n=3)
    g = split([t] + runs)
    assert len(g["main"]) == 1 and len(g["headless"]) == 3 and not g["subagent"]


def test_ttl_choice_follows_the_gaps(write_jsonl):
    # 10-minute gaps: every call would miss on 5m, so 1h wins despite the dearer writes
    slow = _interactive(write_jsonl, gap=600)
    sim = best_ttl([slow], price_for)
    assert sim["1h"].cost_usd < sim["5m"].cost_usd
    # 20-second gaps: nothing ever expires, 5m writes are simply cheaper
    fast = _interactive(write_jsonl, gap=20, name="sess-2.jsonl", sid="sess-2")
    sim = best_ttl([fast], price_for)
    assert sim["5m"].cost_usd < sim["1h"].cost_usd


def test_headless_runs_prefer_5m(write_jsonl):
    runs = _headless(write_jsonl)
    sim = best_ttl(runs, price_for, "headless")
    assert sim["5m"].cost_usd < sim["1h"].cost_usd


def test_window_sweep_and_knee(write_jsonl):
    t = _interactive(write_jsonl, gap=30, n=120)  # grows to ~600k
    prof = Profile([200] * 10, 0.0)
    sweep = window_sweep([t], price_for, prof, grid=[100_000, 200_000, 400_000])
    assert [r.window for r in sweep] == [100_000, 200_000, 400_000]
    assert sweep[0].compactions > sweep[-1].compactions > 0
    assert all(r.saved_usd > 0 for r in sweep)
    pick = pick_window(sweep, floor=150_000)
    assert pick.window in (200_000, 400_000)
    assert pick_window(sweep, floor=10_000_000) is None


def test_dead_writes_count_what_nobody_reads(write_jsonl):
    t = _interactive(write_jsonl, gap=30, n=5)
    runs = _headless(write_jsonl, n=2, spacing=5)
    dw = dead_writes([t] + runs, price_for)
    # the session's last 4k write and both one-shot prompts (10k each) are never read back
    assert dw.tokens == 4_000 + 20_000
    assert dw.premium_usd > 0 and dw.dominant == "headless"


def test_idle_compaction_nets_the_rebuild(write_jsonl):
    lines = [claude_line(0, 0, w1h=300_000, model="claude-opus-5-5")]
    lines.append(claude_line(1, 2 * 3600, w1h=300_000, model="claude-opus-5-5"))  # 2h idle, full rebuild
    t = load(str(write_jsonl(lines)), "claude")
    r = idle_compaction([t], price_for, 200_000, 50_000)
    assert r.idle_gaps == 1 and r.compactions == 1 and r.net_usd > 0
    assert idle_compaction([t], price_for, 400_000, 50_000).compactions == 0


def test_tune_cli_and_apply(write_jsonl, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    _interactive(write_jsonl, gap=30, n=120)
    _headless(write_jsonl, n=5)
    root = str(tmp_path / "projects")
    assert main(["tune", "--path", root, "--since", T0.isoformat(), "--no-color"]) == 0
    out = capsys.readouterr().out
    assert "autoCompactWindow" in out and "claude -p" in out and "--apply" in out
    assert main(["tune", "--path", root, "--since", T0.isoformat(), "--json"]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["settings"]["autoCompactWindow"] >= 100_000
    assert main(["tune", "--path", root, "--since", T0.isoformat(), "--apply", "--no-color"]) == 0
    settings = json.loads((tmp_path / "cfg" / "settings.json").read_text(encoding="utf-8"))
    assert settings["autoCompactWindow"] == plan["settings"]["autoCompactWindow"]
    assert main(["tune", "--reset"]) == 0
    assert "autoCompactWindow" not in json.loads((tmp_path / "cfg" / "settings.json").read_text(encoding="utf-8"))


def test_statusline_uses_claude_code_fields_without_transcript():
    payload = {
        "model": {"id": "claude-opus-5-5"},
        "context_window": {"current_usage": {"input_tokens": 500, "cache_creation_input_tokens": 2_000,
                                             "cache_read_input_tokens": 180_000, "output_tokens": 500}},
        "prompt_cache": {"warm": True, "ttl": "1h", "expires_at": 1_000_000 + 1_500},
    }
    warm = render(payload, now=1_000_000)
    assert "25:00" in warm and "183k" in warm and "rebuild" in warm
    payload["prompt_cache"]["warm"] = False
    assert "cold" in render(payload, now=1_000_000)


def test_cold_cache_makes_compaction_more_attractive():
    p = price_for("claude-opus-5-5")
    prof = Profile([400] * 10, 0.01)
    warm = advise(300_000, p, ttl=TTL_1H, calls_so_far=100, growth_per_call=0, profile=prof)
    cold = advise(300_000, p, ttl=TTL_1H, calls_so_far=100, growth_per_call=0, profile=prof, cold=True)
    assert cold.cold and cold.compact_cost > warm.compact_cost  # full-price read instead of a cache read
    assert cold.compact_saving > warm.compact_saving  # ...but continuing would rebuild at 2x


def test_statusline_cold_hint_says_first(write_jsonl, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    p = _interactive(write_jsonl, gap=30, n=40)  # ~280k context, 1h writes
    last = p.steps[-1].ts.timestamp()
    cold = render({"transcript_path": p.path}, now=last + 2 * 3600)
    assert "cold" in cold and "/compact first saves" in cold
    warm = render({"transcript_path": p.path}, now=last + 60)
    assert "/compact saves" in warm and "first" not in warm


def test_subagent_starts_spot_parallel_cold_launches(write_jsonl):
    subs = []
    for i in range(4):
        read = 0 if i < 3 else 11_000  # three launched together (cold), one later (warm prefix)
        line = claude_line(i, i, read=read, w1h=11_000 if read == 0 else 500, inp=6_000, model="claude-opus-5-5", sid="s")
        subs.append(load(str(write_jsonl([line], name=f"agent-{i}.jsonl", sub="s/subagents")), "claude"))
    r = subagent_starts(subs, price_for)
    assert r.count == 4 and r.cold == 3 and r.staggerable == 2 and r.stagger_saving_usd > 0
    assert r.base <= 17_000


def test_lone_subagent_cold_start_cannot_be_staggered(write_jsonl):
    line = claude_line(0, 0, read=0, w1h=11_000, inp=6_000, model="claude-opus-5-5", sid="s")
    t = load(str(write_jsonl([line], name="agent-solo.jsonl", sub="s/subagents")), "claude")
    r = subagent_starts([t], price_for)
    assert r.cold == 1 and r.staggerable == 0 and r.stagger_saving_usd == 0
    # two launched 10 minutes apart: the second had a warm prefix to read, so it is cold by its own doing
    late = claude_line(1, 600, read=0, w1h=11_000, inp=6_000, model="claude-opus-5-5", sid="s")
    t2 = load(str(write_jsonl([late], name="agent-late.jsonl", sub="s/subagents")), "claude")
    assert subagent_starts([t, t2], price_for).staggerable == 0
