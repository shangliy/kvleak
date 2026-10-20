from conftest import claude_line

from kvleak import analyze, classify, load, price_for
from kvleak.sources import read_generic


def _session(write_jsonl, *calls, **kw):
    return load(str(write_jsonl([claude_line(i, *c[:1], **c[1]) for i, c in enumerate(calls)], **kw)))


def test_streaming_duplicates_are_deduped(write_jsonl):
    line = claude_line(0, 0, w5=20_000)
    t = load(str(write_jsonl([line, line, line])))
    assert len(t.steps) == 1
    assert t.project == "demo-app"


def test_cold_start_then_warm(write_jsonl):
    t = _session(write_jsonl, (0, dict(w5=30_000)), (20, dict(read=30_000, w5=500)))
    misses = classify(t)
    assert [m.cause for m in misses] == ["cold_start"]


def test_ttl_expiry_after_idle_gap(write_jsonl):
    t = _session(write_jsonl, (0, dict(w5=50_000)), (10, dict(read=50_000, w5=1_000)), (10 + 420, dict(w5=52_000)))
    (cold, ttl) = classify(t)
    assert ttl.cause == "ttl_expiry"
    assert ttl.gap_s == 420
    assert ttl.rebuilt == 51_000 + 3  # previous prompt size
    # sonnet: penalty = rebuilt * (1.25 - 0.1)
    assert abs(ttl.penalty - 51_003 * 1.15) < 1
    assert ttl.keepalive_pings == 1 and ttl.keepalive_saving > 0


def test_one_hour_ttl_is_not_expired_at_seven_minutes(write_jsonl):
    t = _session(write_jsonl, (0, dict(w1h=50_000)), (420, dict(w1h=50_000)))
    assert classify(t)[-1].cause == "prefix_bust"


def test_model_switch(write_jsonl):
    t = _session(write_jsonl, (0, dict(w5=40_000)), (30, dict(w5=40_000, model="claude-opus-5-5")))
    assert classify(t)[-1].cause == "model_switch"


def test_compaction(write_jsonl):
    lines = [claude_line(0, 0, w5=40_000), {"type": "system", "subtype": "compact_boundary"},
             claude_line(1, 30, w5=15_000)]
    t = load(str(write_jsonl(lines)))
    assert classify(t)[-1].cause == "compaction"


def test_subagent_cold_start(write_jsonl):
    p = write_jsonl([claude_line(0, 0, w5=25_000)], name="agent-abc.jsonl", sub="sess-1/subagents")
    t = load(str(p))
    assert t.is_subagent
    assert classify(t)[0].cause == "subagent_cold_start"


def test_small_misses_ignored(write_jsonl):
    t = _session(write_jsonl, (0, dict(w5=2_000)))
    assert classify(t) == []


def test_report_totals(write_jsonl):
    t = _session(write_jsonl, (0, dict(w5=50_000)), (10, dict(read=50_000, w5=1_000)), (700, dict(w5=52_000)))
    r = analyze([t])
    assert r.steps == 3 and r.sessions == 1
    assert r.read == 50_000
    assert 0 < r.hit_rate < 1
    assert r.buckets["ttl_expiry"]["count"] == 1
    assert r.leak_usd > 0
    d = r.to_dict()
    assert d["top_misses"][0]["cause"] in ("ttl_expiry", "cold_start")


def test_generic_openai_style(tmp_path):
    p = tmp_path / "log.jsonl"
    p.write_text(
        '{"created_at": "2026-10-01T00:00:00Z", "model": "gpt-x", "usage": {"prompt_tokens": 30000, '
        '"completion_tokens": 10, "prompt_tokens_details": {"cached_tokens": 0}}}\n'
        '{"created_at": "2026-10-01T00:00:30Z", "model": "gpt-x", "usage": {"prompt_tokens": 31000, '
        '"completion_tokens": 10, "prompt_tokens_details": {"cached_tokens": 30000}}}\n'
    )
    t = read_generic(str(p))
    assert [s.read for s in t.steps] == [0, 30000]
    assert [s.input for s in t.steps] == [30000, 1000]


def test_generic_flat_agent_style(tmp_path):
    p = tmp_path / "transcript.jsonl"
    p.write_text(
        '{"type": "PLANNER_RESPONSE", "input_tokens": 24000, "cache_read_tokens": 0, "created_at": "2026-10-01T00:00:00Z"}\n'
        '{"type": "USER", "text": "hi"}\n'
        '{"type": "PLANNER_RESPONSE", "input_tokens": 500, "cache_read_tokens": 24000, "created_at": "2026-10-01T00:00:20Z"}\n'
    )
    t = load(str(p))
    assert len(t.steps) == 2 and t.steps[1].read == 24000


def test_breakeven_kmax():
    assert price_for("claude-sonnet-5").keepalive_kmax() == 11
    assert price_for("claude-opus-5-5").keepalive_kmax() == 24
    assert price_for("claude-fable-5-1-20261001").keepalive_kmax() == 49
    assert price_for("some-unknown-model").input_per_mtok is None
