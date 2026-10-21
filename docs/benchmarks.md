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
| 100k | 334 | $1,297 | $832 | 66.7% |
| **200k** | **98** (≈3 per session) | $1,297 | **$721** | **57.7%** |
| 400k | 23 | $1,297 | $487 | 40.2% |

The post-compaction size is learned from the 32 real compactions in the same
history (median 51k tokens, not the 33k a naive model assumes), and a session
that really compacted is not credited twice. Both corrections make these
numbers smaller than the ones in the 0.2.0 release notes.

Why it's still so large: with caching, the bill is dominated by *re-reading*
huge contexts. A 450k context on Opus 5.5 costs about $0.17 per call just to
carry, counting cache reads plus a 1–2% miss risk at the 1-hour write price.
Compacting it costs about $0.31 once and pays off after **2 calls**.

> This counts money only. It assumes the summary keeps what the rest of the
> session needs. In practice, compact at natural breaks: after a feature lands,
> before switching tasks, or when the statusline shows `⚠ /compact saves ~$X`.

## 5. Backtest: Claude Code's own settings (`kvleak tune`)

Claude Code 2.1.3xx exposes the knobs behind most of the above. `kvleak tune`
replays the same 30 days under each candidate value (method in
[how-it-works §7](how-it-works.md#7-settings-backtests-kvleak-tune)):

| setting | default | best for this history | saving / 30 d | note |
|---|---|---|---:|---|
| `autoCompactWindow` | auto (~967k on 1M models) | **200k** | **$721 (−57.7%)** | 98 compactions over 33 sessions; the largest window that keeps 80% of the best saving |
| `promptCacheTtl` (main) | 1h on a subscription, 5m on an API key | 1h | $0 (already 1h) | 5m would have cost **+$1,884**: 739 hits in the 5–60 min band would become misses |
| `subagentPromptCacheTtl` | 5m | 5m | $0 | 1h would cost +$112; subagents rarely idle |
| `CLAUDE_CODE_PROMPT_CACHE_TTL` for `claude -p` batches | 1h | **5m** | **$169 (−36%)** | 20,535 one-shot runs; the prompt is written at 2× and never read back |
| `CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS` | 200k | 100k | $20 | idle compaction itself nets $212 of the $347 idle-gap leak at the default threshold |
| **total** | | | **$909 (−45% of $2,036)** | |

The full bill for these 30 days, by component, explains why the auto-compact
window dominates: of $1,850 spent on interactive sessions, **40% was cache
reads** and **45% cache writes**, against 15% output and under 0.1% uncached
input. Caching works; what costs money is carrying a median 161k (and up to
1M) of context on every call.

## 6. What filled the context (`kvleak diet --all`)

The same 33 sessions, every block that entered the context, calibrated to the
API's usage numbers (method in [how-it-works §8](how-it-works.md#8-context-diet-kvleak-diet)):

| source | items | tokens | share | carried cost |
|---|---:|---:|---:|---:|
| Bash results (what came back from the shell) | 7,760 | 6.1M | **29.2%** | **$298** |
| Bash inputs (scripts and heredocs the model sent to the shell) | 7,761 | 4.5M | **21.5%** | **$205** |
| thinking (exact; kept in context until compaction on current models) | 6,472 | 3.0M | 14.2% | $140 |
| your messages | 1,891 | 2.0M | 9.6% | $82 |
| system prompt & tools (≈40k per session) | 33 | 1.3M | 6.3% | $62 |
| Write inputs (files the model wrote out) | 349 | 999k | 4.8% | $47 |
| Read results (many of them screenshots, priced by pixels) | 421 | 925k | 4.4% | $39 |
| assistant text | 3,761 | 875k | 4.2% | $37 |
| everything else | ≈1,300 | 300k | 1.4% | $11 |
| **total** | | **20.8M** | | **$958** |

Thinking is exact (`usage.output_tokens_details.thinking_tokens`); the other
rows share the remaining measured growth in proportion to their size, so their
split is approximate but their sum is not. (0.4.0 weighted replies by their
full `output_tokens` and attributed 59% to "assistant output"; that was an
artefact of the estimate, corrected here.)

The expected villain, whole-file reads, is 4%. Half of the context is the
shell: the output that came back and the scripts that went in. The biggest
single blocks are pasted documents (40k tokens carried for 500 calls ≈ $6
each) and `cat` of generated tables.

**Thinking per call, from the same history.** Opus 5.5: 238 tokens at
`medium`, 299 at `high`; Fable 5.1 at `high`: 497. Thinking stays in context
on these models, so it is billed 5× as output and then re-read on every call
until a compaction. Even so, running every `high` Opus call at `medium` would
have saved **$3** in 30 days. Effort is not the lever here; compaction is.

**Long turns.** 114 tool loops ran 20 or more calls before the user spoke
again (longest: 246). Each call re-read the history the loop started with:
4,707 calls, **$351**. Had each loop run in a fresh subagent (≈20k starting
context, report back a few hundred tokens), **$319** of that would not have
been spent, and the loop's own output would have accumulated in the subagent's
context instead of the main one.

**`bashOutputMaxChars`.** Capping inline shell output at 8,000 characters
(the rest goes to a file the model can still read) would have saved **$32**.

**Dead writes.** $207 of the 30-day bill was the cache-write premium on tokens
nothing ever read back: $206 of it in one-shot `claude -p` runs, where every
prompt is written at the 1h rate (2×) for a single use. That is 42% of what
those runs cost, hence the 5m TTL recommendation for batch scripts.

## 7. Steps, not tokens: mechanical chains and `kvleak refine`

The same 33 sessions, re-read for *how many calls* did the work rather than
how many tokens each carried (method in
[how-it-works §8](how-it-works.md#8-context-diet-kvleak-diet), "Mechanical
chains"):

| | |
|---|---:|
| billed calls | 9,924 |
| calls inside 20+ call loops | 4,754 (48%) |
| mechanical chains (≥3 consecutive ls/cat/grep/read steps, <200 thinking tokens, <300 chars of reply) | **493** |
| calls inside those chains | **2,235 (23%)** |
| upper bound saved had each chain been one script | **$160** |

The five largest chains are polling loops the model ran one `until`/`ls`/`date`
at a time against a 450–800k context (up to 19 steps, $5.43 for one chain),
and a `sed -n` walk through a paper, 8 to 17 lines at a time. Each step cost
more to *show up* than to do.

`kvleak refine` turns these numbers into rules and keeps only the ones that
clear $5 of evidence in the window:

| rule | evidence on this history | worth / 30 d |
|---|---|---:|
| `loops-in-subagents` | 116 loops of 20+ calls re-read 4,754 calls of history for $353 | $320 |
| `batch-steps` | 493 chains, 2,235 calls | $160 |
| `scripts-in-files` | inline scripts were 21% of the context, carried for $206 | $103 (half, to be conservative) |
| `shell-output` | 270 Bash results over 8k chars | $32 |
| `edit-not-write` | Written files 5% of the context, largest 27k tokens | $24 |
| `terse-in-loops` | reply text 4%: **below the 8% gate, not proposed** | – |

Together about $640 per 30 days, against a cost of ≈150 tokens per call to
carry the block. `--probe` results will be added here once a week of
sessions has run under the rules; the maintainer's sessions before that date
are the baseline.

The `PostToolUse` hook would have acted on the same 270 results as the
`bashOutputMaxChars 8000` row of `kvleak tune` ($32), with the difference that
the model keeps the error lines and a path instead of a cut-off.

## 8. A/B benchmark: accuracy and cost with and without kvleak

`python bench/run.py` (method in [bench/README.md](../bench/README.md)): six
generated, self-grading tasks, three seeds each, four arms, 72 `claude -p`
runs on Claude Opus 5.5 (default effort, 80-turn and $4 caps, none hit).
`baseline` is stock Claude Code; `hooks` adds `kvleak hooks --install`'s four
hooks via `--settings`; `rules` adds the five `kvleak refine` rules to the
repo's `CLAUDE.md`; `kvleak` is both. Cost is Claude Code's own
`total_cost_usd` estimate at list prices.

| arm | accuracy (18 runs) | cost / run | calls / run | context carried / run | Bash result tokens / run | cheaper than baseline |
|---|---:|---:|---:|---:|---:|---:|
| baseline | **1.00** | $0.21 | 5.0 | 135k | 5,464 | – |
| hooks | **1.00** | $0.21 (+3%) | 5.3 | 145k | 5,642 | 6 of 18 pairs |
| rules | **1.00** | $0.20 (−5%) | 4.5 | 120k | 3,772 | 10 of 18 pairs |
| kvleak | **1.00** | $0.20 (−2%) | 4.9 | 131k | 4,003 | 8 of 18 pairs |

Per task, `baseline → kvleak` (three seeds each): build-error $0.21 → $0.18
(−16%, 17k-line build log; Bash result tokens 8.7k → 3.5k), log-hunt $0.25 →
$0.21 (−17%, 130k-line log), the other four tasks −7% to +19%.

What this says, and what it does not:

* **Accuracy is untouched.** 72 runs, every one of them scored 1.0, in every
  arm. Condensing shell output and adding the rules block did not make the
  model miss anything these tasks check (including an error buried in the
  middle of a 17k-line build log).
* **On short tasks the cost effect is inside the noise.** Every task took
  4–7 calls and 20–30 seconds. Opus 5.5 already pipes long output through
  `tail`/`head` on its own: the Bash hook fired in only **3 of 18** runs per
  hooks arm (the build logs). The `rules` arm is the best of the four (−5%
  mean, −1.7% median, half a call less per run), but with three seeds a −5%
  mean is not a result to quote; the per-pair spread is ±20%.
* **The levers kvleak measures on real history are not reachable here.**
  Compaction windows, idle compaction, long loops in subagents and the TTL
  choices all act on sessions of hundreds of calls and hours of wall time;
  the 30-day numbers in §4–§7 come from those. A 5-call task has nothing to
  compact and no loop to move. The honest reading is: the hooks and rules are
  safe (no accuracy cost, no run slowed down), they pay in proportion to how
  much long output a task produces, and the big savings need the long-session
  settings from `kvleak tune`.

Spend for the whole benchmark: $14.78 at list price (subscription quota in
practice). Raw per-run metrics: `bench/results/round2/results.json`.

## 9. Learned keep-alive budget (`kvleak keepalive`)

The break-even in §2 says pinging *can* pay for up to `K_max` pings. Whether
it *does* depends on whether you come back while the entry is warm. On the
same 30 days as §1 (`kvleak keepalive --since 30d`, 1 h TTL, mostly Opus 5.5,
read 0.05×, write 2×):

| | |
|---|---|
| Pauses that outlived the TTL (prefix ≥ 10k) | 323, of which 164 ended the session |
| Came back within 1 ping / 2 / 5 / 14 (prefix-weighted) | 25% / 42% / 57% / 77% |
| Saving of "ping up to k, then give up" vs. never pinging | k=1 $157 · k=2 $261 · k=5 **$337** (budget) · k=14 $413 (optimum) · k=79 $268 |
| Pauses begun 12–14h | 109; 75% never came back; budget 4 pings |
| Pauses begun 06–08h | 14; 14% never came back; budget 2 pings |
| Pauses begun 21–23h | 46; 39% never came back; budget 11 pings (overnight returns) |

The curve is flat past 5 pings: the optimum at 14 is driven by a few overnight
returns and would cost almost three times the requests for the last fifth of
the saving, so the recommended budget is the knee (80% of the optimum). This
is larger than the §1 "Recoverable" line ($90) because that line only counts
heavy misses and only gaps under 2 h; the policy accounting here counts every
pause, including the ones where pinging was wasted.

Method: [how-it-works.md §5a](how-it-works.md#5a-learned-keep-alive-budget-kvleak-keepalive).
Caveats: list prices (quota on a subscription); the return rates are one
person's habits; a pause inside a session includes long tool runs, which a
keep-alive bridges just as well.
