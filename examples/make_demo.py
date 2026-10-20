"""Generate a synthetic Claude Code-style transcript set for trying kvleak.

    python examples/make_demo.py demo-logs && kvleak --path demo-logs
"""

import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

random.seed(7)
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "demo-logs")
START = datetime(2026, 10, 12, 9, 0, tzinfo=timezone.utc)
n = 0


def call(f, sid, t, model, ctx, new, cold, sub=False):
    global n
    n += 1
    usage = {
        "input_tokens": 4,
        "cache_creation_input_tokens": (ctx + new) if cold else new,
        "cache_read_input_tokens": 0 if cold else ctx,
        "output_tokens": random.randint(150, 1500),
        "cache_creation": {"ephemeral_5m_input_tokens": (ctx + new) if cold else new, "ephemeral_1h_input_tokens": 0},
    }
    line = {"type": "assistant", "sessionId": sid, "isSidechain": sub, "cwd": f"/work/{proj}",
            "requestId": f"req_{n}", "timestamp": t.isoformat().replace("+00:00", "Z"),
            "message": {"id": f"msg_{n}", "model": model, "usage": usage}}
    f.write(json.dumps(line) + "\n")


for s in range(14):
    proj = random.choice(["checkout-api", "web-dashboard", "ml-pipeline"])
    sid = f"{s:02d}{random.getrandbits(40):010x}-demo"
    d = OUT / f"-work-{proj}"
    d.mkdir(parents=True, exist_ok=True)
    t = START + timedelta(hours=s * 7)
    model = "claude-opus-5-5"
    ctx = 24_000
    with open(d / f"{sid}.jsonl", "w") as f:
        call(f, sid, t, model, 0, ctx, cold=True)
        for _ in range(random.randint(40, 160)):
            gap = random.choice([8, 15, 30, 45, 90]) if random.random() > 0.03 else random.choice([400, 900, 1800, 3600])
            t += timedelta(seconds=gap)
            new = random.randint(300, 6000)
            switch = random.random() < 0.015
            if switch:
                model = "claude-sonnet-5-5" if model == "claude-opus-5-5" else "claude-opus-5-5"
            bust = random.random() < 0.01
            call(f, sid, t, model, ctx, new, cold=gap >= 300 or switch or bust)
            ctx += new
        for a in range(random.randint(0, 4)):
            sd = d / sid / "subagents"
            sd.mkdir(parents=True, exist_ok=True)
            with open(sd / f"agent-{a}.jsonl", "w") as g:
                tt, c2 = t, 0
                call(g, sid, tt, "claude-haiku-5-5", 0, 26_000, cold=True, sub=True)
                c2 = 26_000
                for _ in range(random.randint(3, 12)):
                    tt += timedelta(seconds=10)
                    nn = random.randint(500, 4000)
                    call(g, sid, tt, "claude-haiku-5-5", c2, nn, cold=False, sub=True)
                    c2 += nn
print(f"wrote {n} calls to {OUT}/")
