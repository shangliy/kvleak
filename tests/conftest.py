import json
from datetime import datetime, timedelta, timezone

import pytest

T0 = datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc)


def claude_line(i, at, read=0, w5=0, w1h=0, inp=3, out=200, model="claude-sonnet-5", sid="sess-1", dup=False, diag=None):
    """`diag`: the API's cache_miss_reason dict Claude Code records under message.diagnostics."""
    line = {
        "type": "assistant",
        "sessionId": sid,
        "cwd": "/home/me/src/demo-app",
        "requestId": f"req_{i}",
        "timestamp": (T0 + timedelta(seconds=at)).isoformat().replace("+00:00", "Z"),
        "message": {
            "id": f"msg_{i}",
            "model": model,
            "usage": {
                "input_tokens": inp,
                "cache_creation_input_tokens": w5 + w1h,
                "cache_read_input_tokens": read,
                "output_tokens": out,
                "cache_creation": {"ephemeral_5m_input_tokens": w5, "ephemeral_1h_input_tokens": w1h},
            },
        },
    }
    if diag is not None:
        line["message"]["diagnostics"] = {"cache_miss_reason": diag}
    return line


@pytest.fixture
def write_jsonl(tmp_path):
    def _write(lines, name="sess-1.jsonl", sub=""):
        d = tmp_path / "projects" / "-home-me-src-demo-app" / sub
        d.mkdir(parents=True, exist_ok=True)
        p = d / name
        p.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
        return p

    return _write
