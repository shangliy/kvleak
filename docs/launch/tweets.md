# 10 standalone tweets

Each ≤ 280 chars and stands alone. Post the launch thread (en.md) first and pin it,
then one of these a day. Attach docs/assets/demo.gif, report.png, or a statusline
recording where noted.

---

**1 · Hook**
My Claude Code cache hit rate over 30 days: 95.4%.
Input spend that still leaked to cache rebuilds: 20.7%.

Hit rate counts tokens. Your bill counts misses × context size.

`uvx kvleak` shows the real number in 3 seconds.
github.com/shangliy/kvleak

---

**2 · The asymmetry**
Prompt caching math nobody shows you:

200k context, 1h cache, Fable 5.1
• one hit: $0.05
• one rebuild: $3.95

One miss = 80 hits. Newer models make reads cheaper, so every miss hurts *more*.

`kvleak breakeven 200k --model claude-fable-5-1 --ttl 1h`

---

**3 · The 2% stat**
I audited 30 days of my own Claude Code transcripts.

2% of model calls carried more than half of all uncached input.

Your cache isn't failing often. It's failing *big*. [attach report.png]

---

**4 · Tokens ≠ cost**
"60–90% fewer tokens" tools shrink what the context carries. Carrying is paid at cache-read price, the cheapest line on the bill.

2,848 billed Claude Code runs: −38% tool output, +6.8% cost (arXiv 2607.12161).

`kvleak whatif` replays each tool's promise on *your* bill.

---

**5 · Four settings (attach demo.gif)**
Claude Code already has the settings that fix most of the leak: compaction window, cache TTLs, idle compaction, Bash output cap. Defaults tuned for nobody.

`kvleak tune` replays your 30 days under every value. `--apply` writes the winners. −45% input spend on mine.

---

**6 · Lunch is expensive**
69% of my prompt-cache leak came from walking away.

Claude Code caches for 1h now, so the expensive gaps are lunch, meetings, overnight.

Idle 84 min → $8.92 rebuild. One message at minute 59 → $0.11.

---

**7 · Statusline (attach statusline recording)**
A cache countdown in the Claude Code status bar:

● cache 59:47 · 249k ctx · rebuild $1.99 · ⚠ /compact saves ~$9.42

Ticks while you read. Flags when carrying the context costs more than shrinking it.

`kvleak statusline --install`

---

**8 · Keep-alive budget**
Keep-alive pings keep the cache warm, but each one costs a cache read, and most of my lunch pauses never come back (75%).

`kvleak keepalive` learns from your own pauses how many pings pay before giving up, by time of day. Mine: 5 overall, 4 at lunch, 11 at night.

---

**9 · Did it help?**
The question every optimizer dodges.

`kvleak tune --apply` records the day. A week later `kvleak probe` contrasts your own sessions before vs after: input per call, leak share, compactions. Measured on what was billed, not replayed.

---

**10 · CTA**
kvleak:
• zero deps, no API key, no network
• reads Claude Code, Codex CLI, Gemini CLI, OpenCode
• `--fail-over 15` makes it a CI gate for agent jobs

3 seconds to find out what your agent really costs:
`uvx kvleak`
Post yours: github.com/shangliy/kvleak/issues/1
