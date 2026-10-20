# kvleak

**Find out where your AI coding agent leaks prompt-cache money, and how to stop it.**  ·  [中文](README.zh-CN.md)

[![CI](https://github.com/shangliy/kvleak/actions/workflows/ci.yml/badge.svg)](https://github.com/shangliy/kvleak/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/kvleak)](https://pypi.org/project/kvleak/)
![Python](https://img.shields.io/badge/python-3.9%2B-blue)
![Dependencies](https://img.shields.io/badge/dependencies-0-brightgreen)
![License](https://img.shields.io/badge/license-MIT-green)

> **A 99.0% cache hit rate, and the session still leaked $82.** On Claude Fable 5.1 one
> cache miss costs as much as 80 hits. In a 30-day audit of real Claude Code use, 2% of calls
> carried more than half of all uncached input. [See the numbers →](docs/benchmarks.md)

Your agent probably reports a 90%+ cache hit rate, and you are still overpaying.
A cache miss costs about **12× more than a hit**, and the misses are rare but huge.
They come from idle gaps that outlive the 5-minute TTL, from model switches, and
from subagents that start cold. In real audits, **about 5% of calls burn about
half of all uncached input**.

`kvleak` reads the transcripts that are already on your disk, finds every
expensive miss, says *why* it happened, and puts a dollar figure on it.

```bash
uvx kvleak          # or: pipx run kvleak  ·  pip install kvleak
```

No API key, no config, no network calls, no dependencies. It reads
`~/.claude/projects` by default and any JSONL with token usage via `--path`.

```text
 kvleak · prompt-cache leak report
 14 sessions + 18 subagents · 1,594 model calls · 10.0k min miss size

  Cache hit rate    92.1%  ██████████████████████
  Input spend       $145.09   caching already saved you $791.64
  Leaked to misses   $69.54   47.9% of input spend went to cache rebuilds and cold starts
  Recoverable        $27.00   with break-even-capped keep-alive pings alone

 Where the leak comes from
  cause                            misses  uncached    leak  share
  Idle gap outlived the cache TTL      48     10.0M  $38.40  55.2%  ████████▊
  Model switched mid-session           21      5.2M  $18.34  26.4%  ████▏
  Prefix changed within the TTL        16      2.6M  $11.14  16.0%  ██▌
  New session cold start               14      336k   $1.61   2.3%  ▎
  Subagent cold start                  18      468k  $0.054   0.1%

 What to do about it
  1. Idle gaps are your biggest leak class. Reply before the TTL ring empties, ...
  2. Switching models throws away the whole cache (KV state is per model). ...
```

<sub>Output from the bundled synthetic demo: `python examples/make_demo.py demo && kvleak --path demo`</sub>

---

## Real numbers

| | |
|---|---|
| 30 days of interactive Claude Code sessions | **97.5%** hit rate, but **27.5%** of input spend went to cache rebuilds |
| Share of that leak caused by idle gaps | **82%** (median gap 2.8 h; Claude Code now uses the 1 h TTL) |
| Cost of one miss on a 200k context | **$3.95** on Fable 5.1, $1.56 on Opus 5.5 (1 h TTL) |
| 6-worker swarm, pointers + warm workers + pinned models | **−86%** input cost in simulation |
| Compaction advisor replayed on real history (compact only above 200k) | **−58%** input spend with 98 compactions in 30 days (≈3 per session); **−40%** with 23 above 400k |
| `kvleak tune --apply` on the same 30 days | **−$909 (−45%)** from four settings Claude Code already has; auto-compact at 200k instead of ~967k is **−58%** alone |

Methodology and reproduction commands: [docs/benchmarks.md](docs/benchmarks.md).

<p align="center"><img src="https://raw.githubusercontent.com/shangliy/kvleak/main/docs/assets/report.png" width="720" alt="kvleak HTML report"></p>

## What you get

| Command | What it does |
|---|---|
| `kvleak` | Audits every session: hit rate, dollars leaked, leak broken down by cause, leakiest sessions, worst single misses, and concrete next steps. |
| `kvleak --since 7d --html report.html` | A self-contained HTML report (light/dark, no JS) you can share with your team. `--markdown` and `--json` also work. |
| `kvleak session <id>` | Call-by-call timeline of one session, with each miss flagged and explained. |
| `kvleak statusline --install` | Adds a live **cache TTL countdown** to Claude Code: `◕ cache 3:41 · 182k ctx · rebuild $0.89`. |
| `kvleak advise` | Tells you whether to **`/compact` or hand off to a fresh session now**, and when that pays for itself. `--backtest` replays your history with the advisor in the loop. |
| `kvleak tune --apply` | Backtests Claude Code's own cache settings (auto-compact window, cache TTLs, idle compaction) on your history and **writes the winners to `settings.json`**. |
| `kvleak breakeven 150k --idle 20m` | Tells you whether to keep this cache alive or let it die. |
| `kvleak pointer build.log --status FAIL` | Turns a huge file into a ~60-token hand-off message for multi-agent boards. |
| `kvleak --fail-over 15` | Exits with code 2 when more than 15% of input spend leaked, so it can gate CI or nightly jobs. |

### Every miss gets a cause

| Cause | What happened | Typical fix |
|---|---|---|
| **Idle gap outlived the TTL** | You were away for more than 5 min (or 1 h), so the next message rebuilt everything. | Watch the statusline countdown, batch work, or use capped keep-alive pings. |
| **Prefix changed within the TTL** | Something near the top of the prompt changed: a timestamp, unsorted JSON, or a different tool list. | Make the prefix deterministic. |
| **Model switched mid-session** | KV caches are per model, so a switch always means a full-price rebuild. | Pin one model per session or worker and hand off with pointers. |
| **History compacted** | `/compact` or auto-compact rewrote the history. | Compact once at a natural break, not mid-task. |
| **Cold start / subagent cold start** | A fresh agent paid full price for its system prompt and tool schemas. | Reuse warm workers; keep system prompts identical. |

Only the **avoidable** part counts as leak: the context the cache already held,
priced at write cost minus read cost. A new user message or new tool output
would be paid for anyway, so it is not counted. Details:
[docs/how-it-works.md](docs/how-it-works.md).

## Live TTL countdown in Claude Code

```bash
kvleak statusline --install      # backs up settings.json; refuses to overwrite an existing statusLine without --force
```

```text
◕ cache 3:41 · 182k ctx · rebuild $0.89        (green → yellow → red as the TTL drains)
○ cache cold · next msg rebuilds 182k ($0.89)
```

When the ring is almost empty, sending your next message *now* refreshes the
TTL at the cost of a cache read. If you already have a statusline script, call
`kvleak statusline` from it; it reads the same JSON on stdin.

## When is a big context costing more than it saves?

Caching makes a long context cheap, but not free. Every call re-reads the whole
prefix at the cache-read price, and every miss rebuilds all of it. So a 400k-token
session costs you on *every* call. Compacting costs money once and makes every
later call cheaper:

```
pays off after  R* = one-time cost / ((C − S) · (read + miss_rate · (write − read)))  calls
```

`kvleak advise` fills that in for your newest session. It learns how long your
sessions usually run, and how often they miss, from your own history:

```text
$ kvleak advise

  933c7e07 · my-project · claude-opus-5-5
  Context now          207k  (+1.0k/call)
  Carrying it costs    $0.077 per call (cache read + 2.2% miss risk)
  Calls so far         106  ·  expected still to come ≈ 130 (from your history)

  option                      new context  one-time cost  pays off after  expected saving
  /compact                          33.0k         $0.265       4.1 calls            $8.20
  hand-off doc + new session        28.0k         $0.125       1.9 calls            $8.58

  → Write a short hand-off doc and start a fresh session (or /compact; both pay off within 4 calls).
```

The statusline shows the same advice live: `● cache 59:47 · 249k ctx · rebuild $1.99 · ⚠ /compact saves ~$9.42`.

In a backtest on 30 days of real sessions, an advisor that only knew the past
would have cut input spend by **58%** compacting about 3 times per session
(only above 200k context), or by **40%** compacting 23 times in total (only
above 400k). See [docs/benchmarks.md](docs/benchmarks.md#4-backtest-compaction-advisor).
These numbers cover money only. Compact when the summary would still carry what
the rest of the session needs.

## Let Claude Code do it: `kvleak tune`

Claude Code can compact for you (`autoCompactWindow`), pick the cache TTL
(`promptCacheTtl`, `subagentPromptCacheTtl`) and compact idle sessions before
the cache dies (`CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS`). The defaults are tuned
for nobody in particular. `kvleak tune` replays **your** last 30 days under each
value and shows what it would have saved:

```text
$ kvleak tune

  setting                                         now           best   saving / 30d  why
  autoCompactWindow                               auto (~967k)  200k        $720.52  98 compactions over 33 sessions instead of 32 · −57.7% of input spend
  promptCacheTtl                                  1h            1h                –  33 interactive sessions: 1h costs $1,347, 5m costs $3,231
  subagentPromptCacheTtl                          5m            5m                –  271 subagents: 5m costs $274, 1h costs $386
  `claude -p` runs: CLAUDE_CODE_PROMPT_CACHE_TTL  1h            5m          $169.11  20535 one-shot runs: 5m costs $301, 1h costs $471 · set it in the batch script
  CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS             200k          100k         $19.72  153 idle gaps ≥54 min · compacting there nets $212 at 200k, $232 at 100k
  subagent cold starts                            271 × 19.8k   smaller           –  $40.23 on first calls · omitClaudeMd: true, a `tools:` allowlist, or context: fork

  Total if applied: $909.35 per 30d
  Write these to ~/.claude/settings.json: kvleak tune --apply  (undo: kvleak tune --reset)
```

`--apply` backs up `settings.json` first; `--reset` removes exactly the keys it
wrote. `--min-window 300k` keeps auto-compaction from firing below 300k if you
would rather keep more detail. The audit also reports **dead writes**: tokens
written at the 1h cache-write premium (2×) that nothing ever read back. For
one-shot `claude -p` batches that is about 42% of their bill, which is why the
5m TTL wins there.

## Should I keep this cache alive?

```text
$ kvleak breakeven 100k --model claude-sonnet-5 --idle 20m

  Cached prefix 100k on claude-sonnet-5 (5m TTL)
  Rebuild if it expires   125k tok-equiv  ($0.250)
  ...of which avoidable   115k tok-equiv  ($0.230)
  One keep-alive ping     10.0k tok-equiv  ($0.020)
  Break-even              11 pings ≈ 54m30s of idle time

  Idle 20m00s: 4 pings cost 40.0k tok-equiv  ($0.080), saving 75.0k tok-equiv  ($0.150). Keep it alive.
```

The rule is `K_max = floor((write − read) / read)`: 11 pings at 0.10× reads,
24 at 0.05×, 49 at 0.025×. Past that, let the cache expire.

## Running multi-agent swarms?

Multi-agent and multi-model setups multiply leaks in three ways:

* caches never cross models
* shared boards make every agent re-read everyone's logs, which costs O(N²)
* one-shot subagents each pay a cold-start tax

**[The multi-agent cache playbook](docs/playbook.md)** describes six layers of
practice: pinned models with pointer hand-offs, a script → small model → large
model funnel, warm workers with TTL batching, a slim board contract,
deterministic prefixes, and deliberate compaction. It includes a contract you
can paste straight into your swarm's instructions, and `kvleak` measures each
layer.

```bash
$ kvleak pointer /tmp/build.log --status FAIL --note "3 tests failing in //pkg"
{"bytes":40211,"lines":512,"note":"3 tests failing in //pkg","path":"/tmp/build.log","sha256":"9f2c1e07a1b4c3d2","status":"FAIL","tail":[...]}
```

## Supported logs

| Source | How |
|---|---|
| **Claude Code** (incl. subagents) | automatic: `~/.claude/projects`, respects `$CLAUDE_CONFIG_DIR` |
| **Any JSONL with usage counters** | `--path <dir-or-file>`. Auto-detects Anthropic (`cache_read_input_tokens`), OpenAI (`prompt_tokens_details.cached_tokens`), Gemini (`cachedContentTokenCount`) and flat agent-framework keys (`input_tokens`, `cache_read_tokens`, `created_at`). |

Prices for current Claude models are built in (`kvleak models`). For other
providers, add a pricing file; see [`examples/pricing.json`](examples/pricing.json).
Models without a price still get full token-level analysis.

Want your agent harness supported natively? A reader is one small function
that yields `Step`s. See `src/kvleak/sources.py` and [CONTRIBUTING.md](CONTRIBUTING.md).

## Use it as a library

```python
from kvleak import analyze, load

report = analyze([load("session.jsonl")])
print(report.hit_rate, report.leak_usd, report.buckets["ttl_expiry"])
```

## FAQ

**Is my data sent anywhere?** No. kvleak only reads local files and makes no
network calls.

**I'm on a subscription, not the API. Does this matter?** Yes. Cache misses
use up your rate-limit headroom the same way they would cost money on the API.
Read the dollar figures as relative weights.

**My hit rate is 95%. Am I fine?** Maybe not. Hit rate counts tokens, not
dollars, and one 800k-token rebuild weighs as much as thousands of cheap hits.
Look at the *Leaked* line instead.

**How accurate is the cause detection?** It follows the rules in
[docs/how-it-works.md](docs/how-it-works.md). The main blind spot is very long
generations: timestamps are written when a response ends, so a few TTL
expiries can show up as prefix busts.

## Credits

Inspired by the cache-countdown idea in
[olddonkey/cache-refresher](https://github.com/olddonkey/cache-refresher) and by
production audits of multi-agent swarms.

MIT licensed. Contributions welcome!
