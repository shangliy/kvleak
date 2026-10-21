import json

from conftest import T0, claude_line

from kvleak import load
from kvleak.cli import main
from kvleak.probe import Snapshot, contrast, first_applied, load_events, projected_saving_30d, record, snapshot

MODEL = "claude-opus-5-5"
DAY = 86400


def _session(write_jsonl, sid, start_s, ctx, calls, compact_at=None):
    lines = [claude_line(0, start_s, w1h=ctx, model=MODEL, sid=sid)]
    for i in range(1, calls):
        if compact_at is not None and i == compact_at:
            lines.append({"type": "system", "subtype": "compact_boundary", "sessionId": sid})
            lines.append(claude_line(i, start_s + i * 20, w1h=ctx // 3, model=MODEL, sid=sid))
            continue
        lines.append(claude_line(i, start_s + i * 20, read=ctx, w1h=1_000, model=MODEL, sid=sid))
    return load(str(write_jsonl(lines, name=sid + ".jsonl")))


def test_record_and_load_events(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    assert load_events() == [] and first_applied([]) is None
    record("tune", at=100.0)
    record("hooks", at=200.0)
    ev = load_events()
    assert [e["what"] for e in ev] == ["tune", "hooks"] and first_applied(ev) == 100.0


def test_snapshot_contrast_and_projection(write_jsonl):
    at = T0.timestamp() + 10 * DAY
    before = [_session(write_jsonl, "b%d" % i, i * DAY, 300_000, 10) for i in range(3)]
    after = [_session(write_jsonl, "a%d" % i, 10 * DAY + i * DAY, 120_000, 10, compact_at=5) for i in range(3)]
    ts = before + after
    b = snapshot(ts, at - 30 * DAY, at)
    a = snapshot(ts, at, at + 3 * DAY)
    assert b.sessions == 3 and a.sessions == 3 and b.calls == 30 and a.calls == 30
    assert b.context_per_call > a.context_per_call
    assert b.compactions_per_session == 0 and a.compactions_per_session == 1
    assert b.input_per_call > a.input_per_call
    rows = {r.label: r for r in contrast(b, a)}
    assert rows["context per call"].delta < -0.5 and rows["compactions per session"].delta is None
    proj = projected_saving_30d(b, a)
    assert abs(proj - (b.input_per_call - a.input_per_call) * 30 * (30 / 3)) < 1e-6 and proj > 0
    assert Snapshot().input_per_call == 0.0 and projected_saving_30d(Snapshot(), Snapshot()) == 0.0


def test_probe_cli(tmp_path, monkeypatch, capsys, write_jsonl):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    root = str(tmp_path / "projects")
    assert main(["probe", "--path", root, "--no-color"]) == 1  # nothing recorded, no --at
    for i in range(3):
        _session(write_jsonl, "b%d" % i, i * DAY, 300_000, 10)
    for i in range(3):
        _session(write_jsonl, "a%d" % i, 10 * DAY + i * DAY, 120_000, 10, compact_at=5)
    at = (T0.timestamp() + 10 * DAY)
    from datetime import datetime, timezone

    date = datetime.fromtimestamp(at, tz=timezone.utc).isoformat()
    assert main(["probe", "--path", root, "--no-color", "--at", date]) == 0
    out = capsys.readouterr().out
    assert "kvleak probe" in out and "before: 3 sessions" in out and "after: 3 sessions" in out
    assert "context per call" in out and "less" in out
    assert main(["probe", "--path", root, "--no-color", "--at", date, "--json"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["after"]["sessions"] == 3 and d["projected_saving_30d_usd"] > 0
    # recorded apply date is used when --at is absent; one after-session is "too early"
    record("tune", at=T0.timestamp() + 12 * DAY + 1)
    assert main(["probe", "--path", root, "--no-color"]) == 0
    assert "Too early" in capsys.readouterr().out
