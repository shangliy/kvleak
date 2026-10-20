# 10 tweets

Each one stands alone (≤ 280 chars). Post #1 first, then one or two a day. Attach the report
screenshot (docs/assets/report.png) or a statusline GIF where noted.

---

**1 · Hook (pin this)**
My Claude Code session had a 99.0% cache hit rate.
It still leaked $82 to cache rebuilds.

Hit rate counts tokens. Your bill counts misses × context size.

I built a tool that shows the real number: `uvx kvleak`
github.com/shangliy/kvleak

---

**2 · The asymmetry**
Prompt caching math nobody shows you:

200k-token context, 1h cache
• one hit: $0.05 on Fable 5.1
• one miss: $3.95

One miss = 80 hits. Newer models make reads cheaper, so every miss hurts *more*.

`kvleak breakeven 200k --model claude-fable-5-1 --ttl 1h`

---

**3 · The 2% stat**
I audited 30 days of my own Claude Code transcripts.

2.2% of model calls carried 54% of all uncached input.

Your cache isn't failing often. It's failing *big*.

`uvx kvleak --since 30d` 📊 [attach report.png]

---

**4 · Lunch is expensive**
82% of my prompt-cache leak came from walking away.

Claude Code now caches with a 1h TTL, so the 5-min expiry isn't the problem. Lunch, meetings and overnight are. Median gap before a miss: 2.8 hours.

Real case: idle 84 min → $8.92 rebuild. One message at minute 59 → $0.11.

---

**5 · Statusline (attach GIF)**
I put a cache countdown in my Claude Code status bar:

◕ cache 3:41 · 452k ctx · rebuild $9.04 · ⚠ /compact saves ~$84

When the ring is about to empty, one short message resets it for a cache read.

`uvx kvleak statusline --install`

---

**6 · Compaction surprise**
Counter-intuitive: a big context costs you on every call, even when it hits.

450k tokens on Opus 5.5 ≈ $0.17 per call just to carry (cache read + miss risk).
/compact costs ≈ $0.31 once → pays off after 2 calls.

`kvleak advise` tells you when.

---

**7 · Backtest**
I replayed 30 days of my sessions with a compaction advisor in the loop. It only saw the past at each step.

Auto-compact at 200k instead of the default ~967k:
→ ~3 compactions per session
→ −58% input spend

Claude Code has the setting. `kvleak tune --apply` picks the value from your own history and writes it.

---

**8 · Multi-agent**
Running agent swarms? Three things multiply your token bill:

1. KV caches never cross models
2. A log pasted on a shared board is re-read by every agent, every turn (O(N²))
3. Every fresh subagent pays ~28k tokens of cold-start prefill

The playbook: github.com/shangliy/kvleak/blob/main/docs/playbook.md

---

**9 · Pointer trick**
The cheapest multi-agent optimization I know: stop pasting logs.

`kvleak pointer build.log --status FAIL`
→ ~60 tokens: {path, sha256, lines, tail}

Only the agent fixing the build opens the file. In a 6-worker simulation: −62% input cost from this alone.

---

**10 · CTA / CI**
kvleak:
• zero dependencies, no API key, no network
• reads Claude Code + any JSONL with token usage (OpenAI/Gemini-style too)
• `--fail-over 15` turns it into a CI gate for agent jobs

3 seconds to find out what your agent really costs:
`uvx kvleak`
github.com/shangliy/kvleak
