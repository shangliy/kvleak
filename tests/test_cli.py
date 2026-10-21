import json

from conftest import T0, claude_line

from kvleak.cli import main
from kvleak.pointer import make_pointer
from kvleak.statusline import render


def _fixture(write_jsonl):
    return write_jsonl([
        claude_line(0, 0, w5=50_000),
        claude_line(1, 10, read=50_000, w5=1_000),
        claude_line(2, 900, w5=52_000),
        claude_line(3, 930, read=52_000, w5=800, model="claude-opus-5-5"),
    ])


def test_audit_terminal(write_jsonl, capsys):
    p = _fixture(write_jsonl)
    assert main(["--path", str(p.parent), "--no-color"]) == 0
    out = capsys.readouterr().out
    assert "Cache hit rate" in out and "Idle gap outlived" in out


def test_audit_json_and_files(write_jsonl, tmp_path, capsys):
    p = _fixture(write_jsonl)
    html, md = tmp_path / "r.html", tmp_path / "r.md"
    assert main(["audit", "--path", str(p), "--json", "--html", str(html), "--markdown", str(md)]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["steps"] == 4
    assert data["buckets"]["ttl_expiry"]["count"] == 1
    assert "<html" in html.read_text(encoding="utf-8") and "kvleak report" in md.read_text(encoding="utf-8")


def test_fail_over_gate(write_jsonl):
    p = _fixture(write_jsonl)
    assert main(["--path", str(p), "--json", "--fail-over", "1"]) == 2


def test_session_view(write_jsonl, capsys):
    p = _fixture(write_jsonl)
    assert main(["session", "sess-1", "--path", str(p.parent), "--no-color"]) == 0
    assert "Idle gap outlived" in capsys.readouterr().out


def test_breakeven(capsys):
    assert main(["breakeven", "100k", "--model", "claude-sonnet-5", "--idle", "20m", "--no-color"]) == 0
    out = capsys.readouterr().out
    assert "11 pings" in out and "Keep it alive" in out


def test_statusline_warm_and_cold(write_jsonl):
    p = _fixture(write_jsonl)
    last = T0.timestamp() + 930
    warm = render({"transcript_path": str(p)}, now=last + 60)
    cold = render({"transcript_path": str(p)}, now=last + 400)
    assert "4:00" in warm and "rebuild" in warm
    assert "cold" in cold
    assert "cache" in render({})


def test_pointer(tmp_path):
    f = tmp_path / "build.log"
    f.write_text("\n".join(f"line {i}" for i in range(500)))
    ptr = make_pointer(str(f), tail=2, status="FAIL")
    assert ptr["lines"] == 500 and ptr["tail"] == ["line 498", "line 499"] and ptr["status"] == "FAIL"


def test_statusline_install_is_safe(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"statusLine": {"type": "command", "command": "my-own.sh"}, "theme": "dark"}))
    assert main(["statusline", "--install"]) == 1  # refuses to clobber
    assert "my-own.sh" in settings.read_text()
    assert main(["statusline", "--install", "--force"]) == 0
    data = json.loads(settings.read_text())
    assert "kvleak" in data["statusLine"]["command"] and data["theme"] == "dark"
    assert data["statusLine"]["refreshInterval"] == 5  # ticks while idle; Claude Code only re-runs on events
    assert (tmp_path / "settings.json.kvleak.bak").exists()
    assert main(["statusline", "--install", "--refresh", "0"]) == 0  # idempotent, events only
    assert "refreshInterval" not in json.loads(settings.read_text())["statusLine"]
    assert main(["statusline", "--uninstall"]) == 0
    assert "statusLine" not in json.loads(settings.read_text())


def test_concentration_and_card(write_jsonl, tmp_path, capsys):
    from kvleak.card import render_card

    p = _fixture(write_jsonl)
    svg = tmp_path / "card.svg"
    assert main(["audit", "--path", str(p), "--json", "--card", str(svg)]) == 0
    data = json.loads(capsys.readouterr().out)
    k = data["concentration"]
    assert k["leaking_sessions"] == 1 and k["top5_share"] == 1.0 and k["sessions_for_half"] == 1
    text = svg.read_text(encoding="utf-8")
    assert text.startswith("<svg") and "Cache hit rate" in text and "Idle gap outlived" in text and "uvx kvleak" in text
    # the card escapes whatever lands in it
    from kvleak.analyze import analyze
    from kvleak.sources import load

    r = analyze([load(str(p))])
    hostile = render_card(r, "<script>")
    assert "<script>" not in hostile and "&lt;script&gt;" in hostile
    assert "last 7 days" in render_card(r, "last 7 days")
