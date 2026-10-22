from conftest import claude_line

from kvleak import load
from kvleak.advisor import Profile, advise, advise_steps, backtest, optimal_window
from kvleak.cli import main
from kvleak.pricing import TTL_1H, TTL_5M, price_for


def test_breakeven_formula():
    p = price_for("claude-opus-5-5")
    a = advise(450_000, p, ttl=TTL_1H, calls_so_far=200, growth_per_call=0, profile=Profile([], 0.02))
    r_eff = 0.05 + 0.02 * (2.0 - 0.05)
    one_time = 450_000 * 0.05 + 8_000 * 5 + 8_000 * 2.0
    assert abs(a.compact_breakeven - one_time / ((450_000 - 33_000) * r_eff)) < 1e-6
    assert a.best in ("compact", "handoff") and a.compact_saving > 0


def test_small_context_never_compacts():
    a = advise(40_000, price_for("claude-sonnet-5"), profile=Profile([], 0.02))
    assert a.best is None


def test_session_near_its_end_keeps_going():
    # history says sessions end right after 120 calls -> nothing left to amortise over
    prof = Profile([121] * 10, 0.0)
    a = advise(150_000, price_for("claude-sonnet-5"), calls_so_far=120, growth_per_call=0, profile=prof)
    assert a.expected_remaining == 1 and a.best is None


def test_expected_remaining_uses_median_and_cap():
    prof = Profile(sorted([10, 20, 30, 40, 50, 5000]))
    assert prof.expected_remaining(5) == 35  # median of longer sessions is 40
    assert prof.expected_remaining(1000) == 300  # Lindy fallback, capped


def _big_session(write_jsonl):
    lines, ctx = [claude_line(0, 0, w1h=150_000, model="claude-opus-5-5")], 150_000
    for i in range(1, 80):
        lines.append(claude_line(i, i * 20, read=ctx, w1h=3_000, model="claude-opus-5-5"))
        ctx += 3_000
    return write_jsonl(lines)


def test_backtest_saves_on_long_big_session(write_jsonl):
    t = load(str(_big_session(write_jsonl)))
    b = backtest([t], Profile([], 0.02), price_for)
    assert b["compactions_suggested"] >= 1 and b["saved_usd"] > 0 and 0 < b["saved_pct"] < 1
    assert advise_steps(t.steps, price_for("claude-opus-5-5")).best


def test_advise_cli(write_jsonl, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    p = _big_session(write_jsonl)
    assert main(["advise", "--path", str(p.parent), "--no-color"]) == 0
    out = capsys.readouterr().out
    assert "pays off after" in out and "→" in out
    assert main(["advise", "--backtest", "--path", str(p.parent), "--no-color"]) == 0
    assert "compactions" in capsys.readouterr().out


def test_optimal_window_square_root_law():
    p = price_for("claude-opus-5-5")
    s = 33_000
    w1, w2 = optimal_window(p, TTL_1H, 2_000, 0.02), optimal_window(p, TTL_1H, 4_000, 0.02)
    assert abs((w2 - s) / (w1 - s) - 2 ** 0.5) < 0.01  # doubling the growth rate widens the window by sqrt 2
    assert w1 > s and optimal_window(p, TTL_5M, 2_000, 0.02) > s


def test_expected_remaining_mean_sees_the_tail():
    prof = Profile(sorted([10, 20, 30, 40, 50, 5000]))
    assert prof.expected_remaining(5, "median") == 35
    assert abs(prof.expected_remaining(5, "mean") - (5 + 15 + 25 + 35 + 45 + 300) / 6) < 1e-9
