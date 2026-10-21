import json

from conftest import T0, claude_line

from kvleak import load
from kvleak.advisor import Profile
from kvleak.cli import main
from kvleak.keepalive import OPEN_SESSION_S, budget, budgets, pauses
from kvleak.pricing import TTL_1H, TTL_5M, price_for
from kvleak.statusline import render

NOW = T0.timestamp() + 30 * 24 * 3600
MODEL = "claude-opus-5-5"  # read 0.05, write 5m 1.25: Kmax 24


def _session(gaps, sid, prefix=200_000):
    """One session: a warm call, then one call after each gap (seconds)."""
    lines, at = [claude_line(0, 0, w5=prefix, model=MODEL, sid=sid)], 0
    for i, g in enumerate(gaps, 1):
        at += g
        lines.append(claude_line(i, at, read=prefix, w5=2_000, model=MODEL, sid=sid))
    return lines


def test_pauses_keep_only_gaps_past_the_ttl_and_closed_session_ends(write_jsonl):
    p = write_jsonl(_session([60, 600, 60], "s1"))  # one 10-minute gap on a 5m TTL
    ps = pauses([load(str(p))], now=NOW)
    gaps = sorted((x.gap_s if x.gap_s is not None else -1) for x in ps)
    assert gaps == [-1, 600]  # the 10-minute pause and the session end
    assert all(x.ttl == TTL_5M for x in ps)
    # a session whose last call is recent is still open, not a "never came back"
    ps_open = pauses([load(str(p))], now=T0.timestamp() + 60 + 600 + 60 + 100)
    assert [x.gap_s for x in ps_open] == [600]


def test_budget_recommends_pinging_when_you_come_back(write_jsonl):
    # 8 sessions that pause 8 minutes (1 ping bridges it), each ending once. Pinging once
    # saves 1.2x on the 8 returns and costs 0.05x on the 8 ends -> clearly positive.
    ts = [load(str(write_jsonl(_session([480, 60], "s%d" % i), name="s%d.jsonl" % i))) for i in range(8)]
    b = budget(pauses(ts, now=NOW), price_for, TTL_5M)
    assert b.pauses == 16 and b.never_returned == 8 and b.kmax == 24
    assert b.rows[0].saving_usd == 0
    assert b.recommended == 1
    one = b.rows[1]
    assert abs(one.returned - 0.5) < 0.01  # prefix-weighted; the ending call carries 2k more
    # prefixes: 200,203 at the pause that came back, 202,203 at the session end (+2k write, +3 input, +200 output)
    expect = 8 * 200_203 * (1.25 - 0.05 - 0.05) - 8 * 202_203 * 0.05
    assert abs(one.saving - expect) < 1 and abs(one.saving_usd - expect * 4 / 1e6) < 0.01
    assert b.rows[2].saving_usd < one.saving_usd  # a second ping buys nothing here


def test_budget_says_let_it_expire_when_you_never_return(write_jsonl):
    ts = [load(str(write_jsonl(_session([60], "e%d" % i), name="e%d.jsonl" % i))) for i in range(6)]
    b = budget(pauses(ts, now=NOW), price_for, TTL_5M)
    assert b.never_returned == 6 and b.recommended == 0
    assert all(r.saving_usd <= 0 for r in b.rows)
    assert budgets(ts, price_for, now=NOW)[0].ttl == TTL_5M


def test_keepalive_cli_and_profile(write_jsonl, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    for i in range(5):
        write_jsonl(_session([480, 60], "k%d" % i), name="k%d.jsonl" % i)
    root = str(tmp_path / "projects")
    assert main(["keepalive", "--path", root, "--no-color"]) == 0
    out = capsys.readouterr().out
    assert "pauses outlived the TTL" in out and "/cache-auto on 1" in out
    # --path runs do not touch the profile; a default run would. Simulate the learn step:
    ts = [load(str(f)) for f in (tmp_path / "projects").glob("*/k*.jsonl")]
    prof = Profile.from_transcripts(ts)
    assert prof.keepalive == {"5m": 1}
    prof.save()
    assert Profile.load().keepalive == {"5m": 1}
    raw = json.loads((tmp_path / "cache" / "kvleak" / "profile.json").read_text())
    assert raw["keepalive"] == {"5m": 1}


def test_statusline_shows_ping_budget_only_near_expiry(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("NO_COLOR", "1")
    Profile([], keepalive={"1h": 2}).save()
    now = 1_000_000.0
    payload = {
        "model": {"id": MODEL},
        "context_window": {"current_usage": {"input_tokens": 10, "cache_read_input_tokens": 50_000}},
        "prompt_cache": {"ttl": "1h", "warm": True, "expires_at": now + 90},
    }
    assert "≤2 pings pay" in render(payload, now=now)
    payload["prompt_cache"]["expires_at"] = now + 600
    assert "pings pay" not in render(payload, now=now)
    Profile([], keepalive={"1h": 0}).save()
    payload["prompt_cache"]["expires_at"] = now + 90
    assert "ping" not in render(payload, now=now)
    assert OPEN_SESSION_S == 2 * 3600 and TTL_1H == 3600


def test_by_hour_budget_and_profile_lookup(write_jsonl, tmp_path, monkeypatch):
    from datetime import datetime

    from kvleak.keepalive import by_hour, hour_bucket, learned

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    ts = []
    # pauses at T0 (local hour of T0) come back after 8 min; pauses 6h later never do
    for i in range(8):
        ts.append(load(str(write_jsonl(_session([480, 60], "m%d" % i), name="m%d.jsonl" % i))))
    late = [claude_line(0, 6 * 3600, w5=200_000, model=MODEL, sid="n%d" % i) for i in range(8)]
    for i, ln in enumerate(late):
        ts.append(load(str(write_jsonl([ln, claude_line(1, 6 * 3600 + 60, read=200_000, w5=2_000, model=MODEL, sid="n%d" % i)],
                                       name="n%d.jsonl" % i))))
    ps = pauses(ts, now=NOW)
    hours = dict(by_hour(ps, price_for, TTL_5M))
    h_early = hour_bucket(T0.timestamp())
    h_late = hour_bucket(T0.timestamp() + 6 * 3600 + 60)
    assert hours[h_early].recommended == 1 and hours[h_late].recommended == 0
    overall, per_hour = learned(ts, price_for, now=NOW)
    prof = Profile([], keepalive=overall, keepalive_hours=per_hour)
    assert prof.ping_budget("5m", datetime.fromtimestamp(T0.timestamp()).hour) == 1
    assert prof.ping_budget("5m", datetime.fromtimestamp(T0.timestamp() + 6 * 3600 + 60).hour) == 0
    assert prof.ping_budget("5m", None) == overall["5m"]
    prof.save()
    assert Profile.load().keepalive_hours == per_hour
