from conftest import claude_line

from kvleak import analyze, classify, load, price_for
from kvleak.analyze import recommendations
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


def test_new_tool_output_is_not_a_rebuild(write_jsonl):
    # the second call hit the whole previous prompt and wrote 70k of *new* tool output: no leak
    t = _session(write_jsonl, (0, dict(w5=10_000)), (5, dict(read=10_003, w5=70_000)))
    assert [m.cause for m in classify(t)] == ["cold_start"]


def test_partial_prefix_hit_counts_only_the_lost_part(write_jsonl):
    t = _session(write_jsonl, (0, dict(w5=100_000)), (5, dict(read=60_000, w5=50_000)))
    m = classify(t)[-1]
    assert m.cause == "prefix_bust" and m.rebuilt == 100_003 - 60_000


def test_unmarked_compaction_is_inferred_from_the_shrink(write_jsonl):
    t = _session(write_jsonl, (0, dict(w5=40_000)), (5, dict(read=40_003, w5=1_000)), (10, dict(w5=25_000)))
    assert classify(t)[-1].cause == "compaction"


def test_gemini_prices_have_no_write_premium():
    p = price_for("gemini-2.5-pro")
    assert p.input_per_mtok == 1.25 and p.write_5m_mult == 1.0 and p.write_1h_mult == 1.0 and p.keepalive_kmax() == 9
    assert price_for("gemini-4-argon").read_mult == 0.05
    assert price_for("models/gemini-3.1-pro-preview").name == "gemini-3.1-pro"


def test_cause_table_shows_the_size_of_a_typical_rebuild(write_jsonl, capsys):
    """misses × context size is the leak; the second factor gets its own column and prices the base."""
    from kvleak.cli import main

    lines = [claude_line(0, 0, w5=50_000), claude_line(1, 10, read=50_000, w5=1_000), claude_line(2, 430, w5=52_000)]
    path = write_jsonl(lines)
    r = analyze([load(str(path))])
    assert r.buckets["cold_start"]["rebuilt_median"] >= 50_000
    assert r.buckets["ttl_expiry"]["rebuilt_median"] == 51_003
    assert r.buckets["ttl_expiry"]["rebuilt"] == 51_003
    assert main(["--path", str(path)]) == 0
    out = capsys.readouterr().out
    assert "per miss" in out and "51.0k" in out
    tip = next(t for t in recommendations(r) if t.startswith("New sessions start cold"))
    assert "rebuild a median 5" in tip and "Every 10k" in tip


def test_api_diagnosis_overrides_the_inferred_cause_and_size(write_jsonl):
    """Claude Code records the API's cache_miss_reason; tools_changed with a token count beats a guess."""
    lines = [
        claude_line(0, 0, w5=50_000),
        claude_line(1, 10, read=50_000, w5=1_000),
        claude_line(2, 20, read=20_000, w5=31_000, diag={"type": "tools_changed", "cache_missed_input_tokens": 30_000}),
        claude_line(3, 30, read=10_000, w5=42_000, diag={"type": "unavailable"}),
        claude_line(4, 40, read=52_000, w5=500, diag={"type": "previous_message_not_found"}),
    ]
    t = load(str(write_jsonl(lines)))
    assert t.steps[2].miss_reason == "tools_changed" and t.steps[2].missed == 30_000
    misses = {m.step_index: m for m in classify(t)}
    assert misses[2].cause == "tools_changed" and misses[2].rebuilt == 30_000 and misses[2].api_reason == "tools_changed"
    assert misses[3].cause == "server_unavailable"
    assert 4 not in misses  # a hit with a verdict attached is still a hit
    r = analyze([t])
    assert r.api_diagnosed == 2 and r.buckets["tools_changed"]["count"] == 1


def test_diagnosis_on_a_later_content_block_is_kept(write_jsonl):
    first = claude_line(0, 0, w5=50_000)
    second = claude_line(1, 10, read=10_000, w5=41_000)
    dup = claude_line(1, 10, read=10_000, w5=41_000, diag={"type": "system_changed", "cache_missed_input_tokens": 40_000})
    t = load(str(write_jsonl([first, second, dup])))
    assert len(t.steps) == 2 and t.steps[1].miss_reason == "system_changed"
    assert classify(t)[-1].cause == "system_changed"


def test_prices_sonnet_55_opus_41_and_the_haiku_cliff():
    assert price_for("claude-sonnet-5-5").read_mult == 0.05 and price_for("claude-sonnet-5").read_mult == 0.1
    assert price_for("claude-opus-4-1-20250805").input_per_mtok == 15.0
    assert price_for("claude-opus-4-20250514").input_per_mtok == 15.0
    assert price_for("claude-opus-4-8").input_per_mtok == 5.0
    haiku = price_for("claude-haiku-5-5")
    assert haiku.at(90_000).input_per_mtok == 0.10 and haiku.at(150_000).input_per_mtok == 0.50
    assert price_for("gemini-2.5-pro").at(250_000).input_per_mtok == 2.5
