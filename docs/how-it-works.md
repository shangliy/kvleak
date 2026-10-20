# How kvleak works

## 1. Read billed calls

Each transcript becomes a list of calls. Every call has these counters:
`input` (uncached), `write_5m`, `write_1h`, `read`, `output`, a timestamp and a
model.

* **Claude Code** writes one line per content block and repeats the `usage` on
  each line. kvleak dedupes by `(message.id, requestId)`. Subagent transcripts
  live under `<session>/subagents/` and are flagged. `compact_boundary` markers
  flag the next call as following a compaction.
* **Generic JSONL** auto-detects common key names: `input_tokens` /
  `prompt_tokens` / `promptTokenCount`, `cache_read_input_tokens` /
  `cache_read_tokens` / `cached_tokens`, and so on. OpenAI and Gemini count
  cached tokens inside the prompt total, so kvleak subtracts them.

## 2. Find heavy misses

A call is a **heavy miss** when both of these hold:

* `uncached ≥ --min-miss` (default 10k tokens)
* `read < uncached` (the call mostly missed)

## 3. Give each miss a cause

Rules are checked in order and the first match wins:

| cause | rule |
|---|---|
| `cold_start` / `subagent_cold_start` | first call of a transcript |
| `compaction` | a compaction marker comes right before the call |
| `model_switch` | the model differs from the previous call's model |
| `ttl_expiry` | the gap since the previous call is at least the TTL minus 10 s. The TTL is 1 h if the previous writes were mostly 1-hour writes, otherwise 5 min. |
| `prefix_bust` | everything else: the prefix changed while the cache should still have been warm |

## 4. Price each miss

All math is in **base-equivalent tokens**, meaning tokens billed at the model's
normal input price. They are converted to dollars only when the model's price
is known.

```
rebuilt  = min(uncached, previous call's prompt size)   # context we already had
eff_rate = (input·1 + write_5m·w5 + write_1h·w1h) / uncached
leak     = rebuilt · (eff_rate − read_mult)              # paid on top of a cache hit
```

New content (a fresh user message, new tool results) is not counted as leak,
because you would pay to write it either way.

## 5. Keep-alive counterfactual

For every `ttl_expiry` miss with idle gap `g`:

```
interval = TTL − 30 s
pings    = ceil((g − TTL) / interval)        # pings needed to bridge the gap
saving   = leak − pings · rebuilt · read_mult   if pings ≤ K_max else 0
```

The sum of these savings is the **Recoverable** line in the report.

## 6. Compaction advisor (`kvleak advise`)

Carrying a context of `C` tokens for one more call costs `C · r_eff`, where
`r_eff = read + m · (write − read)` and `m` is your historical rate of heavy
misses per call. Compacting to `S` tokens (system prompt + summary) costs once:

```
compact  = C · read            # the compaction call reads the whole history
         + summary · output    # the model writes the summary
         + summary · write     # the next call caches it
R*       = compact / ((C − S) · r_eff)
```

Remaining calls `R` come from your history: the median of `(L − n)` over past
sessions longer than the current `n` calls, capped at 300. With too little
history, the Lindy rule (`R ≈ n`) is used instead. The advisor recommends acting
when `C ≥ --min-context` (100k by default) and the expected saving over `R`
calls, with the context still growing at its recent rate, is positive. A
hand-off (a short doc and a fresh session) uses the same math with a smaller
`S`.

**Backtest.** `kvleak advise --backtest` walks every past session call by call.
The advisor sees only the past. When it fires, each later call's prompt is
shrunk by the tokens dropped, priced at what that call actually paid per token,
whether it was a hit or a miss. This counts money only. It cannot see quality
lost in the summary, or rework caused by it.

## 7. Settings backtests (`kvleak tune`)

Claude Code exposes a handful of settings that move cache money. `tune` replays
your last 30 days under each candidate value and reports the difference:

| setting | how it is replayed |
|---|---|
| `autoCompactWindow` | The compaction backtest of §6, but the rule is "compact whenever the context reaches W" for each W in 100k … 800k. The recommended W is the largest one that still captures 80% of the best saving (fewest compactions for most of the money); `--min-window` sets a floor. The post-compaction size is the median of your real compactions. |
| `promptCacheTtl` | Every call is re-priced under a 5m and a 1h TTL. A gap shorter than the TTL is a hit (new tokens at that TTL's write price, the rest read), a longer gap rebuilds the prompt. Misses that were not TTL-caused stay as they happened. |
| `subagentPromptCacheTtl` | Same replay over subagent transcripts. |
| `CLAUDE_CODE_PROMPT_CACHE_TTL` for `claude -p` batches | One-shot runs share a prefix across runs, so the replay orders them by time and rewrites the shared prefix whenever the gap between runs exceeds the TTL. The prompt itself is written once and never read back under either TTL, so the cheaper write usually wins. |
| `CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS` | For every idle gap of 54 min or more in a 1h-TTL session: the rebuild that idle compaction avoids minus what the compaction costs, at the default 200k threshold and at 100k. |

**Dead writes.** A cache write costs 1.25× (5m) or 2× (1h) the plain input
price. When nothing reads that entry before it expires (the last call of a
session, a call right before a long gap, or a one-shot `claude -p` prompt), the
premium was wasted. The audit sums it and names the workload it comes from.

## Caveats

* Claude Code timestamps are written when a response finishes, not when the
  request starts. The TTL counts from the request start, so very long
  generations can make a real TTL expiry look like a `prefix_bust`.
* Dollar figures use public list prices (`kvleak models`). Discounts, batch
  pricing and subscriptions are not modelled. Treat dollars as relative
  weights. On a subscription plan, a leak costs rate-limit headroom rather than
  money.
