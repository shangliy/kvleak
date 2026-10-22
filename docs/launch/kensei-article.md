# Raw tokens fell 40–72%. The bill went up 14–37%. A re-read of a public 945-run Claude Code benchmark

*Paste-ready for dev.to / Medium. A shorter Reddit version and a one-line hook are at the end.
Every number below is reproducible from the dataset's own JSON logs; the script is in the appendix.*

---

Last week [@0xKense1](https://github.com/kenseii1/claude-code-limits-tests) published something rare:
945 headless Claude Code runs, interleaved arms, three repetitions per question, and every raw log
on GitHub. The article compared `/compact`, `/clear` and "just keep going" by counting tokens, and
found what everyone expects: `/compact` cuts tokens by 40–72%.

I ran the same logs through the `costUSD` that Claude Code itself writes into each result. The
ranking flips.

## The table

Three follow-up questions after forking a session at N = 5, 15 or 30 turns, Sonnet 5.5, 1-hour
cache TTL, three reps each. Cost is what Claude Code billed, per call, from its own `modelUsage`.

| History | Context at fork | Arm | Raw tokens, 3 questions | Billed, 3 questions | Break-even for `/compact` |
|---:|---:|---|---:|---:|---|
| 5 turns | 51k | continue | 539,809 | **$0.141** | |
| | | `/clear` | −47% | +4% | |
| | | `/compact` | −39% | **+37%** | never |
| 15 turns | 107k | continue | 1,119,496 | **$0.223** | |
| | | `/clear` | −76% | **−50%** | |
| | | `/compact` | −58% | **+14%** | ~7 questions |
| 30 turns | 155k | continue | 1,366,859 | **$0.239** | |
| | | `/clear` | −79% | **−41%** | |
| | | `/compact` | −60% | **+19%** | ~8 questions |

The `/compact` row includes the compaction call itself, which is where the money goes.

## Why fewer tokens cost more

Three prices are in play, and raw token counts treat them as one.

- **Carrying context is almost free.** A cache read is 0.1× the input price. The 155k context at
  N = 30 costs about 1.5 cents per question to carry.
- **The summary is written at the most expensive price.** `/compact` makes the model write a
  summary at the output price (5×), then writes the new prefix into a 1-hour cache at 2×. At
  N = 30 that one call is $0.071, more than the three follow-up questions it was meant to save.
- **The summary forgets what was open.** The follow-ups after `/compact` re-read files the old
  context already held, so the per-question cost after compaction ($0.066–0.071) is barely under
  the uncompacted one ($0.074–0.080). Divide the compaction fee by that small gap and you get the
  break-even column: seven or eight more questions before `/compact` has paid for itself.

`/clear` wins at N ≥ 15 because it pays neither the summary nor the re-reads of a half-remembered
context. It just starts over, and a fresh start on this task was cheap.

## The subagent result is the same story

| Experiment | Arm | Raw tokens (4 calls) | Billed | Main call | 3 follow-ups |
|---|---|---:|---:|---:|---:|
| subagent | main thread does it | 255,035 | **$0.117** | $0.052 | $0.065 |
| subagent | delegate to a subagent | −3% | **+30%** | $0.093 | $0.059 |
| subagent-deep | main thread | 303,785 | **$0.160** | $0.127 | $0.034 |
| subagent-deep | subagent | +6% | **+17%** | $0.155 | $0.033 |

A subagent starts with a cold cache and writes its own prefix. That is the $0.052 → $0.093 jump on
the main call. In exchange the parent's context stays lighter, which saves about $0.002 per
follow-up question. Twenty follow-ups to pay back one delegation.

## Two things the logs show that the article did not look for

I also ran `kvleak` over the raw logs, which prices every cache miss in a transcript.

**An 8 h 36 m pause inside two 30-turn chains.** Rate limiting paused the runs between turn 28
(08:07 UTC) and turn 29 (16:43 UTC). Turn 29 rebuilt 142k tokens of context at the write price:
$0.629 for one call, against $0.092 for the call before it. The session still reports a 95.9%
cache hit rate. One miss on a large context is worth about eighty hits, and the hit rate cannot
see it.

**$14 of the $79 benchmark bill was the 1-hour write premium on runs that never read it back.**
Every `claude -p` run writes its prefix into the 1-hour cache at 2× instead of 5-minute cache at
1.25×, then exits. For one-shot harnesses `CLAUDE_CODE_PROMPT_CACHE_TTL=5m` removes that 18%.

## What to do with this

1. **Do not compact small contexts.** Below roughly 100k, `/compact` never pays. Claude Code's
   auto-compact fires near the window limit, which is usually right; the mistake is manual
   `/compact` as hygiene.
2. **If you will ask fewer than seven more questions, `/clear` beats `/compact`.** Or just keep
   going. Carrying is cheap.
3. **Delegate for isolation or parallelism, not for savings.** A subagent costs a cold start.
4. **Set the 5-minute TTL on batch runs.** `CLAUDE_CODE_PROMPT_CACHE_TTL=5m` for anything that
   exits right after its answer.
5. **Count dollars, not tokens.** Three prices, three orders of magnitude between a cache read and
   a summary. Any tool that reports "tokens saved" is reporting the cheapest line on the bill.

## Caveats

Sonnet 5.5 only; three reps per cell, so single-digit percentages are noise; one codebase; the
follow-up questions were short and answerable, which favours `/clear`. A long agentic task that
needs the history would move the break-even. The compaction fee and the cold-start jump are not
noise, they are the price list.

## Reproduce it

Dataset: https://github.com/kenseii1/claude-code-limits-tests (`runs/session/*.json`,
`runs/subagent*/*.json`). Each log carries a `tok` block with the per-call delta of `costUSD`
(`--resume` makes the raw `total_cost_usd` cumulative; the harness already subtracts the previous
call).

```python
import json, glob, re, collections, statistics as st
R = "claude-code-limits-tests/runs"
for N in ("N05", "N15", "N30"):
    rows = {}
    for arm in ("continue", "clear", "compact"):
        per_rep = collections.defaultdict(float)
        for f in glob.glob(f"{R}/session/{N}-{arm}-*q*-r*.json"):
            rep = re.search(r"q(\d)-r", f).group(1)
            per_rep[rep] += json.load(open(f))["tok"]["cost"]
        rows[arm] = st.mean(per_rep.values())
    rows["compact"] += st.mean(json.load(open(f))["tok"]["cost"]
                               for f in glob.glob(f"{R}/session/{N}-compactcmd-*.json"))
    base = rows["continue"]
    print(N, {a: f"{c:.4f} ({(c/base-1)*100:+.0f}%)" for a, c in rows.items()})
```

The miss pricing: `uvx kvleak --path claude-code-limits-tests/runs`, then
`uvx kvleak session 600996ac --path …` for the 8-hour pause.

---

## Reddit version (r/ClaudeAI)

**Title:** Re-priced a public 945-run Claude Code benchmark: /compact cut tokens 40–72% and raised the bill 14–37%

Someone published 945 headless Claude Code runs with every log (kenseii1/claude-code-limits-tests).
The article counted tokens and found /compact saves 40–72%. I re-ran the same logs using the
costUSD Claude Code writes into each result:

- /compact: tokens −39 to −72%, **bill +14 to +37%**. Break-even is 7–8 follow-up questions at
  100–150k context, never at 50k.
- /clear: bill −41 to −50% at 15–30 turns.
- Subagent: tokens −3%, **bill +30%** (cold cache, writes its own prefix). ~20 follow-ups to pay back.
- $14 of the $79 total was the 1h cache-write premium on one-shot runs that exited immediately.
  `CLAUDE_CODE_PROMPT_CACHE_TTL=5m` fixes that.

Why: a cache read is 0.1×, the summary is 5× output + 2× 1h write, and the follow-ups re-read files
the old context already had. Raw token counts treat all of that as one number.

Script and the full table in the writeup (link in comments). Sonnet 5.5, 3 reps per cell, one repo,
so treat single digits as noise. The compaction fee and the cold start are not noise.

## One-line hook (X / HN title)

Re-priced a public 945-run Claude Code benchmark with Claude Code's own costUSD: /compact cut tokens
40–72% and raised the bill 14–37%; a subagent cut tokens 3% and raised it 30%.
