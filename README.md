# kvleak

**Your Claude Code cache hit rate is 95%+. A quarter of your input spend still goes to cache rebuilds. kvleak shows where, and fixes most of it with one command.**  ·  [中文](README.zh-CN.md)

[![CI](https://github.com/shangliy/kvleak/actions/workflows/ci.yml/badge.svg)](https://github.com/shangliy/kvleak/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/kvleak)](https://pypi.org/project/kvleak/)
![Python](https://img.shields.io/badge/python-3.9%2B-blue)
![Dependencies](https://img.shields.io/badge/dependencies-0-brightgreen)
![License](https://img.shields.io/badge/license-MIT-green)

![kvleak audit, whatif and tune on the maintainer's history](docs/assets/demo.gif)

<sub>▶ [Two-minute video](https://github.com/shangliy/kvleak/releases/download/v0.9.1/kvleak-demo.mp4): why the hit rate lies, the leak, the fix, before/after, the benchmark.</sub>

```bash
uvx kvleak            # or: pipx run kvleak  ·  pip install kvleak
```

No API key, no config, no network calls, no dependencies. It reads the session
files **Claude Code, Codex CLI, Gemini CLI and OpenCode** already keep on disk
and is done in seconds. On a subscription the dollars are list-price
equivalents; the percentages are your rate-limit headroom.

```text
 kvleak · prompt-cache leak report
 101 sessions + 276 subagents + 22,100 one-shot runs (Claude Code 81, Codex CLI 20) · 58,208 model calls

  Cache hit rate    95.4%  ██████████████████████▉
  Input spend        $2,602   caching already saved you $21,281
  Output spend      $452.39   replies + thinking at the output price · whole bill $3,054: 43.8% cache writes, 38.8% cache reads, 14.8% output
  Leaked to misses  $537.72   20.7% of input spend went to cache rebuilds and cold starts · 17.6% of the whole bill
  Dead writes       $215.71   write premium on tokens never read back (mostly one-shot `claude -p` runs)

 Where the leak comes from
  cause                            misses  uncached     leak  share
  Idle gap outlived the cache TTL     164     41.3M  $371.92  69.2%  ███████████
  New session cold start            1,378     36.3M   $81.67  15.2%  ██▍
  Subagent cold start                 145      4.1M   $29.44   5.5%  ▉
```

<sub>The maintainer's last 30 days. Yours will differ; that is the point. Post yours in [#1](https://github.com/shangliy/kvleak/issues/1).</sub>

## Turn it on

The audit changes nothing. These four do, each with an undo, and each takes
effect in Claude Code sessions started afterwards:

```bash
pip install kvleak
kvleak tune --apply          # settings.json: compaction window, cache TTLs, idle compaction, Bash cap   (--reset)
kvleak hooks --install       # long shell output stays out of the context; compaction keeps an archive  (--uninstall)
kvleak refine --apply        # context-hygiene rules, with their dollar figures, in ~/.claude/CLAUDE.md (--remove)
kvleak statusline --install  # cache countdown in the status bar, ticking while you read              (--uninstall)
```

How to tell it worked: `/hooks` in Claude Code lists four kvleak entries, the
status bar reads `● cache 59:47 · 86k ctx · rebuild $1.73` after the first
reply, and a long `seq 1 20000` comes back as head + tail + a path instead of
20,000 lines. A week later, `kvleak probe` puts a number on it.

## Why the hit rate lies

A cache hit costs 0.1× the input price (0.025× on Fable 5.1). A rebuild costs
1.25–2×. One miss on a 200k context is worth **80 hits**, so 2% of calls can
carry half of all uncached input and the hit rate never moves. kvleak prices
only the *avoidable* part of each miss: context the cache already held, at
write price minus read price.

## Why "60–90% fewer tokens" did not shrink your bill

Tool-output compressors shrink what the context *carries*, and carrying is
paid at the cache-read price, the cheapest line on the bill. On 2,848 billed
Claude Code runs, a 38% cut in tool output *raised* cost by 6.8%
([arXiv 2607.12161](https://arxiv.org/abs/2607.12161)). `kvleak whatif`
replays each tool's promise on your own bill, as an upper bound:

```text
$ kvleak whatif

  What if · 58,199 model calls · $3,053 over the last 30d
  Bill = $80.38 uncached input + $1,337 cache writes + $1,183 cache reads + $452.12 output

  if you…                who promises it                                     bill saving  share of bill
  kvleak tune --apply    compaction window, TTLs, idle compaction, Bash cap  −$786.56             25.8%
  Shell output −60%      rtk's low end ("60–90%")                            −$222.00              7.3%
  All tool results −40%  AgentDiet, arXiv 2509.23586                         −$183.98              6.0%
  Thinking −25%          effort high → medium                                −$59.22               1.9%
  Startup context −50%   claude-token-optimizer                              −$43.19               1.4%
  Replies half as long   caveman                                             −$32.74               1.1%
```

Compaction changes how much is carried at all. That is why the settings
levers are three times the best compressor, on a history where 44% of the
bill is cache *writes*.

## Three commands

**1. See the leak.** `kvleak` (above). `--since 7d`, `--html report.html` for
a shareable report, `kvleak session <id>` for one session call by call.

**2. Fix the settings.** Claude Code can compact for you, pick the cache TTL
and compact idle sessions, but its defaults are tuned for nobody. `kvleak tune`
replays your last 30 days under every value of each setting and `--apply`
writes the winners to `settings.json` (backed up; `--reset` undoes it):

```text
$ kvleak tune

  setting                                         now           best   saving / 30d  why
  autoCompactWindow                               auto (~967k)  200k        $720.52  98 compactions instead of 32 · −57.7% of input spend
  `claude -p` runs: CLAUDE_CODE_PROMPT_CACHE_TTL  1h            5m          $169.11  42% of their cache writes were never read back
  CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS             200k          100k         $19.72  153 idle gaps ≥54 min
  bashOutputMaxChars                              30,000        8,000        $32.54  Bash results are 29% of what entered the context

  Total if applied: $909 per 30d (−45% of input spend)
  Write these to ~/.claude/settings.json: kvleak tune --apply
```

A week later, `kvleak probe` shows what actually changed: input spend per
call, leak share, context per call, compactions and shell output on your own
interactive sessions before vs after the day you applied, and what the
difference is worth per 30 days. Measured on what was billed, not replayed.

**3. Watch the cache live.** `kvleak statusline --install` puts a TTL
countdown in Claude Code and tells you when carrying the context costs more
than shrinking it:

```text
◕ cache 3:41 · 182k ctx · rebuild $0.89
● cache 59:47 · 249k ctx · rebuild $1.99 · ⚠ /compact saves ~$9.42
◔ cache 1:19 · 250k ctx · rebuild $2.00 · ⚑ ≤2 pings pay
```

## What else is in the box

Each of these is measured on your own history before it suggests anything.
Numbers are the maintainer's 30 days; method and reproduction in
[docs/benchmarks.md](docs/benchmarks.md).

| | | |
|---|---|---|
| `kvleak probe` | Did it help? Splits your sessions at the day `tune --apply` / `hooks --install` / `refine --apply` ran and contrasts input spend per call, leak share, context per call, compactions and shell output before vs after, with a projected 30-day figure | your own before/after, no replay |
| `kvleak advise` | Should you `/compact` or hand off to a fresh session *now*, and after how many calls it pays off | −58% input spend in backtest |
| `kvleak diet` | What filled the context: shell output 29%, scripts sent to the shell 21%, thinking 14%, file reads 4%; the costliest single items named | 114 long tool loops cost $351 to re-read; in subagents −$319 |
| `kvleak hooks --install` | Claude Code hooks that keep long shell output out of the context (head + tail + error lines + a path) and archive everything a compaction drops, so `kvleak recall` can find it | accuracy 1.00 in 72 benchmark runs, −16% on long-output tasks |
| `kvleak refine --apply` | Evidence-gated context-hygiene rules for `CLAUDE.md`, dollar figure written into each; `--probe` later shows which rules changed behaviour | 5 rules covering ≈$640 / 30 d |
| `kvleak keepalive` | How many keep-alive pings your own pauses justify before giving up, overall and by time of day; feeds [cache-refresher](https://github.com/olddonkey/cache-refresher) | 5 pings worth $337; lunch pauses return 25%, 6–8 am ones 86% |
| `kvleak pointer`, `--fail-over`, `--path` | Multi-agent hand-off pointers, a CI gate on leak share, any JSONL with usage counters (Anthropic, OpenAI, Gemini keys) | [playbook](docs/playbook.md) for swarms |

`audit`, `advise`, `keepalive` and `whatif` read Claude Code, Codex CLI
(`~/.codex/sessions`), Gemini CLI (`~/.gemini/tmp/*/chats`) and OpenCode
(`opencode.db`) automatically. The context-level commands (`diet`, `tune`,
`hooks`, `refine`) need the tool results themselves and read Claude Code only.

Full tour with outputs: **[docs/guide.md](docs/guide.md)**. How every number is
computed: [docs/how-it-works.md](docs/how-it-works.md).

## FAQ

**Is my data sent anywhere?** No. Local files only, no network calls.

**I'm on a subscription.** Misses eat the same rate-limit headroom they would
cost in dollars on the API. Read the figures as relative weights.

**Does trimming context hurt the model?** In the A/B benchmark (`bench/`, 72
runs on Opus 5.5) every arm scored 1.0 on every task. The settings `tune`
writes only compact above 200k context; `--min-window` raises that.

## Credits

The countdown idea comes from [olddonkey/cache-refresher](https://github.com/olddonkey/cache-refresher);
hooks, recall and refine borrow from [Prime Agent](https://github.com/PrimeIntellect-ai/prime-agent)
and [Harness²](https://github.com/google-research/harness2). MIT licensed.
