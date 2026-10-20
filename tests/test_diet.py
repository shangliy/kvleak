import base64
import struct
import zlib

from conftest import T0, claude_line

from kvleak.cli import main
from kvleak.diet import (
    IMAGE_MAX_TOKENS,
    SYSTEM_KIND,
    TEXT_KIND,
    THINKING_KIND,
    USER_KIND,
    _image_tokens,
    read_diet,
    tips,
)


def _png(w, h):
    ihdr = struct.pack(">II", w, h) + b"\x08\x02\x00\x00\x00"
    chunk = b"IHDR" + ihdr
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", len(ihdr)) + chunk + struct.pack(">I", zlib.crc32(chunk))


def _assistant(i, at, prompt, out, blocks):
    line = claude_line(i, at, read=prompt - 100, inp=100, out=out, model="claude-opus-5-5")
    line["message"]["content"] = blocks
    return line


def _user(at, content):
    return {"type": "user", "sessionId": "sess-1", "cwd": "/home/me/src/demo-app",
            "timestamp": (T0.replace(second=0)).isoformat(), "message": {"role": "user", "content": content}}


def _session(write_jsonl):
    lines = [
        _user(0, "Please fix the build"),
        _assistant(0, 1, 10_000, 400, [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "pytest -q"}}]),
        _user(2, [{"type": "tool_result", "tool_use_id": "t1", "content": "x" * 40_000}]),  # ~10k tokens
        _assistant(1, 3, 20_400, 300, [{"type": "tool_use", "id": "t2", "name": "Read", "input": {"file_path": "/a/b/big.py"}}]),
        _user(4, [{"type": "tool_result", "tool_use_id": "t2", "content": "y" * 8_000}]),  # ~2k tokens
        _assistant(2, 5, 22_700, 100, [{"type": "text", "text": "done"}]),
        _assistant(3, 6, 22_800, 100, [{"type": "text", "text": "really done"}]),
        _assistant(4, 7, 22_900, 100, [{"type": "text", "text": "really really done"}]),
    ]
    return write_jsonl(lines)


def test_calibration_matches_prompt_growth(write_jsonl):
    d = read_diet(str(_session(write_jsonl)))
    assert d.calls == 5 and d.peak == 22_900
    kinds = d.by_kind()
    # the 10,400 growth at call 1 is split between the 40k-char result and the tiny command
    assert abs(kinds["Bash"]["tokens"] - 10_400) < 100 and kinds["Bash inputs"]["count"] == 1
    assert abs(kinds["Read"]["tokens"] - 2_300) < 100
    assert kinds[USER_KIND]["tokens"] < 10 and abs(kinds[SYSTEM_KIND]["tokens"] - 10_000) < 50  # first prompt is the system prompt
    assert kinds[TEXT_KIND]["count"] == 3
    bash = next(b for b in d.blocks if b.kind == "Bash")
    assert bash.label == "pytest -q" and bash.call == 1 and bash.carried == 3 and bash.carry_usd > 0
    assert next(b for b in d.blocks if b.kind == "Bash inputs").label == "pytest -q"
    assert next(b for b in d.blocks if b.kind == "Read").label == "big.py"
    assert len(d.turns) == 1 and d.turns[0].calls == 5 and not d.long_turns


def test_thinking_is_exact_and_turns_split_on_user_text(write_jsonl):
    lines = [_user(0, "go")]
    for i in range(25):  # one long tool loop...
        a = _assistant(i, i + 1, 50_000 + i * 1_000, 800, [{"type": "tool_use", "id": f"t{i}", "name": "Bash", "input": {"command": "ls"}}])
        a["message"]["usage"]["output_tokens_details"] = {"thinking_tokens": 500}
        a["effort"] = "high"
        lines.append(a)
        lines.append(_user(0, [{"type": "tool_result", "tool_use_id": f"t{i}", "content": "x" * 2_000}]))
    lines.append(_user(0, "thanks, now the other thing"))  # ...then a new request
    lines.append(_assistant(99, 40, 80_000, 100, [{"type": "text", "text": "ok"}]))
    d = read_diet(str(write_jsonl(lines)))
    kinds = d.by_kind()
    assert d.thinking_tokens == 25 * 500 and abs(kinds[THINKING_KIND]["tokens"] - 25 * 500) < 1  # all re-sent later
    assert d.effort[("claude-opus-5-5", "high")] == [25, 12_500]
    assert len(d.turns) == 2 and d.turns[0].calls == 25 and d.turns[1].calls == 1
    long = d.long_turns
    assert len(long) == 1 and long[0].context == 50_000 and long[0].history_usd > long[0].subagent_saving_usd > 0


def test_compaction_stops_the_carry(write_jsonl):
    lines = [
        _user(0, "hi"),
        _assistant(0, 1, 50_000, 100, [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "cat log"}}]),
        _user(2, [{"type": "tool_result", "tool_use_id": "t1", "content": "z" * 400_000}]),
        _assistant(1, 3, 150_100, 100, [{"type": "text", "text": "ok"}]),
        {"type": "system", "subtype": "compact_boundary", "sessionId": "sess-1"},
        _assistant(2, 4, 30_000, 100, [{"type": "text", "text": "after"}]),
        _assistant(3, 5, 30_100, 100, [{"type": "text", "text": "after2"}]),
    ]
    d = read_diet(str(write_jsonl(lines)))
    big = max(d.blocks, key=lambda b: b.tokens)
    assert big.kind == "Bash" and big.carried == 0  # compacted away right after it was read once
    assert d.compactions == 1


def test_images_are_priced_by_pixels():
    small = base64.b64encode(_png(300, 200)).decode()
    huge = base64.b64encode(_png(4000, 4000)).decode()
    assert abs(_image_tokens({"source": {"data": small}}) - 300 * 200 / 750) < 1
    assert _image_tokens({"source": {"data": huge}}) == IMAGE_MAX_TOKENS
    assert _image_tokens({"source": {"data": "not-an-image"}}) == IMAGE_MAX_TOKENS


def test_tips_name_the_dominant_source():
    kinds = {"Bash": {"count": 10, "tokens": 80_000, "carry_usd": 1, "images": 0, "max": 9_000},
             TEXT_KIND: {"count": 10, "tokens": 5_000, "carry_usd": 0.1, "images": 0, "max": 500}}
    out = tips(kinds)
    assert len(out) == 1 and out[0].startswith("Bash results is 94%")
    assert "compaction" in tips({})[0]


def test_diet_cli(write_jsonl, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    p = _session(write_jsonl)
    root = str(tmp_path / "projects")
    assert main(["diet", str(p), "--path", root, "--no-color"]) == 0
    out = capsys.readouterr().out
    assert "Bash results" in out and "pytest -q" in out and "How to shrink it" in out
    assert main(["diet", "--all", "--path", root, "--since", T0.isoformat(), "--no-color"]) == 0
    assert "1 sessions" in capsys.readouterr().out
    assert main(["diet", str(p), "--path", root, "--no-color"]) == 0 and "Thinking" not in capsys.readouterr().out
