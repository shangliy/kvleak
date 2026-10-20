# Numbers

Four sets of numbers, each labelled by where it comes from. Re-run all of them
yourself; every command is on this page.

## 1. Case study: 30 days of one heavy Claude Code user

The maintainer's own `~/.claude/projects`, from 2026-09-21 to 2026-10-20,
audited with `kvleak`. Interactive sessions are those with 5 or more calls,
plus their subagents. Headless one-shot `claude -p` runs are listed separately
because they behave differently.

| | interactive sessions | headless one-shot runs |
|---|---:|---:|
| transcripts | 289 (257 of them subagents) | 20,530 |
| billed model calls | 13,110 | 20,592 |
| cache hit rate (tokens) | **97.5%** | 59.4% |
| input spend at list price | $1,522 | $468 |
| what that input would cost with no caching | $18,085 | $548 |
| **leaked to cache rebuilds** | **$418 (27.5% of input spend)** | $65 (13.8%) |
| heavy misses as a share of calls | **2.2%** | 4.4% |
| uncached input inside those misses | **53.7%** | 11.0% |

Where the interactive leak came from:

| cause | misses | leak | share |
|---|---:|---:|---:|
| Idle gap outlived the cache TTL | 124 | $345 | **82.4%** |
| Subagent cold start | 127 | $27 | 6.5% |
| Prefix changed within the TTL | 17 | $20 | 4.8% |
| Model switched mid-session | 7 | $20 | 4.7% |
| History compacted | 8 | $4 | 0.9% |
| New session cold start | 4 | $3 | 0.7% |

Takeaways:

* **A high hit rate hides the leak.** The leakiest session had a **99.0%**
  token hit rate and still leaked **$82**, because one rebuild of 800k tokens
  outweighs thousands of cheap hits.
* **About 2% of calls carry more than half of all uncached input.**
* **Claude Code now writes about 90% of its cache with the 1-hour TTL**, so the
  5-minute expiry is no longer the problem. The leak is the lunch, meeting and
  overnight gaps: the median idle gap before a TTL miss was 2.8 h. 45 of the
  124 expiries were under 2 h and worth bridging with a keep-alive, which would
  have recovered **$90**.
* Cold starts dominate headless runs, but each one is small. They're cheap
  per call and only add up at volume.

Reproduce on your own machine: `kvleak --since 30d`.

> This is one person's usage, at list API prices. Your mix will differ, and
> that's the point of running it yourself.

## 2. What one cache miss costs, by model

Avoidable cost of rebuilding a **200k-token** context instead of reading it
(`(write − read) × 200k`), at list prices. Source: `kvleak models` and
`kvleak breakeven 200k --model <m> --ttl <ttl>`.

| model | one cache hit | one miss, 5-min TTL | one miss, 1-hour TTL | miss ÷ hit (1h) | keep-alive break-even (5m / 1h) |
|---|---:|---:|---:|---:|---:|
| Claude Fable 5.1 | $0.05 | $2.45 | **$3.95** | **80×** | 49 / 79 pings |
| Claude Opus 5.5 | $0.04 | $0.96 | $1.56 | 40× | 24 / 39 pings |
| Claude Sonnet 5.5 | $0.04 | $0.46 | $0.76 | 20× | 11 / 19 pings |
| Claude Haiku 5.5 | $0.002 | $0.02 | $0.04 | 20× | 11 / 19 pings |

Newer models make cache reads cheaper, so each miss hurts *more* relative to a
hit. On Fable 5.1, a single miss costs as much as 80 hits.

## 3. Simulation: a 6-worker swarm, 40 rounds

`python examples/swarm_sim.py`. This is a transparent toy model, not a
measurement. Every worker re-reads its context each round, and one failing
build log (~8k tokens) is produced per round. Prices are Opus 5.5 with a
5-minute TTL.

| scenario | input (tok-equiv) | cost | vs naive |
|---|---:|---:|---:|
| naive: paste logs on the board, respawn the fixer, reviewer on another model | 17.8M | $71.10 | — |
| + post pointers instead of logs (`kvleak pointer`) | 6.8M | $27.37 | **−62%** |
| + keep the fixer warm instead of respawning | 5.8M | $23.18 | −67% |
| + one model per worker (the [playbook](playbook.md)) | 2.4M | $9.63 | **−86%** |

The board change alone removes most of the O(N²) broadcast cost, because one
pasted log is re-read by every worker on every later turn.

## 4. Backtest: compaction advisor

`kvleak advise --backtest` replays the same 30 days of interactive sessions
(33 main sessions). At every call the advisor sees only the past. When it says
compacting pays, the session is compacted at that point, and every later call
is re-priced with the smaller context at what that call really paid per token.

| compact only above | compactions in 30 days | input spend | saved | share |
|---:|---:|---:|---:|---:|
| 100k | 119 | $1,272 | $1,008 | 80.6% |
| 200k | 39 | $1,272 | $933 | 74.9% |
| **400k** | **10** | $1,272 | **$778** | **63.2%** |

Why it's so large: with caching, the bill is dominated by *re-reading* huge
contexts. A 450k context on Opus 5.5 costs about $0.17 per call just to carry,
counting cache reads plus a 2% miss risk at the 1-hour write price. Compacting
it costs about $0.31 once and pays off after **2 calls**.

> This counts money only. It assumes the summary keeps what the rest of the
> session needs. In practice, compact at natural breaks: after a feature lands,
> before switching tasks, or when the statusline shows `⚠ /compact saves ~$X`.
