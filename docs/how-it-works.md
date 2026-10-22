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

A call is a **heavy miss** when the context it rebuilt is at least `--min-miss`
(default 10k tokens):

```
rebuilt = min(uncached, max(previous prompt − read, 0))   # first call: uncached, if it mostly missed
```

The `read` tokens that hit are a prefix of the previous prompt, so at most
`previous prompt − read` of the context you already had can have been paid for
again. The rest of `uncached` is new content: a 70k tool result arriving on a
call that read the whole previous prompt is not a miss, however large it is.
(Until 0.10 the rule was `uncached ≥ --min-miss and read < uncached`, which
counted such calls.)

## 3. Give each miss a cause

Rules are checked in order and the first match wins:

| cause | rule |
|---|---|
| `cold_start` / `subagent_cold_start` | first call of a transcript |
| `compaction` | a compaction marker comes right before the call, or the prompt shrank to under 75% of the previous one (from at least 20k): Codex, Gemini CLI and OpenCode write no marker |
| `model_switch` | the model differs from the previous call's model |
| `ttl_expiry` | the gap since the previous call is at least the TTL minus 10 s. The TTL is 1 h if the previous writes were mostly 1-hour writes, otherwise 5 min. |
| `prefix_bust` | everything else: the prefix changed while the cache should still have been warm |

## 4. Price each miss

All math is in **base-equivalent tokens**, meaning tokens billed at the model's
normal input price. They are converted to dollars only when the model's price
is known.

```
rebuilt  = min(uncached, max(previous prompt − read, 0))  # context we already had (§2)
eff_rate = (input·1 + write_5m·w5 + write_1h·w1h) / uncached
leak     = rebuilt · (eff_rate − read_mult)               # paid on top of a cache hit
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

The sum of these savings is the **Recoverable** line in the report. It is
conservative: only heavy misses, only gaps under 2 h.

### 5a. Learned keep-alive budget (`kvleak keepalive`)

`K_max` is a ceiling; it says nothing about whether you come back. For every
**pause** (a gap between two calls of a main session longer than the TTL, or
the last call of a session whose transcript has been quiet for 2 h, which
counts as "never came back") with prefix `N` tokens, and for every policy
"ping up to `k` times, then give up":

```
j      = ceil((gap − TTL) / interval)          # pings that would have bridged it; ∞ if it never closed
delta  = N · (write − read) − j · N · read      if j ≤ k      (came back while warm)
       = −k · N · read                          otherwise     (pinged k times for nothing)
```

Summed over all pauses with the same TTL, each priced at its own model, this
is the saving of policy `k` against never pinging. The **optimum** is the `k`
with the largest total; the **budget** kvleak recommends is the smallest `k`
that captures 80% of it, because past the knee the curve is flat, the optimum
wanders with a handful of overnight returns, and each extra ping is a request
of quota on a subscription. The same is computed per 3-hour local bucket of
when the pause began (buckets with ≥8 pauses); `kvleak audit` and
`kvleak keepalive` store both in `profile.json`, and the statusline reads the
bucket for the current hour in the last two minutes of the countdown.

kvleak does not send pings. The refresh must hit the same prefix from inside
Claude Code; cache-refresher does that with `/cache-auto on <k>`.

**Keep-alive versus compaction.** The two decisions meet in the countdown's
last two minutes. If the advisor (§6) says carrying this context costs more
than shrinking it, the statusline shows `⚠ /compact before it expires saves
~$X` and no ping budget: a ping would keep warm exactly the context that
should go, and the compaction call reads the old context at the cache-read
price only while the entry is warm (after expiry it pays the write price for
the same read). The ping budget appears only when there is nothing to compact.

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

Remaining calls `R` come from your history: `(L − n)` over past sessions longer
than the current `n` calls, capped at 300, summarised by the median (the
default) or the mean of the capped values, which is the expectation under the
empirical survival function (`REMAINING_ESTIMATOR`). With too little history,
the Lindy rule (`R ≈ n`) is used instead. The advisor recommends acting when
`C ≥ --min-context` (100k by default) and the expected saving over `R` calls is
positive. The context's growth rate does not enter that comparison: both
branches grow the same way, so a one-off compaction saves `(C − S) · r_eff` per
remaining call whatever the rate. A hand-off (a short doc and a fresh session)
uses the same math with a smaller `S`.

**Where the auto-compact threshold belongs.** Growth matters for the repeated
policy instead. Under "compact to `S` whenever the context reaches `W`, then
regrow at `Δc` per call", each cycle costs one compaction plus the carry of a
context averaging `(S + W) / 2` over `(W − S) / Δc` calls, and the renewal-reward
theorem gives the long-run cost per call its minimum at

```
W* = S + sqrt(2 · Δc · K0 / r_eff),   K0 = S · read + summary · (output + write)
```

the economic-order-quantity square-root law: doubling the growth rate widens
the window by √2. `kvleak advise` prints `W*` for the session's own growth
rate; `kvleak tune` prints it next to the replayed optimum as a cross-check.
The replay wins when they differ: it sees the real miss pattern and the detail
lost at each compaction, which the closed form does not.

**Cold cache.** When the TTL has already expired, the next message rewrites
the whole context at the write price, and a compaction reads it once at the
full uncached price instead of the cache-read price. Both options pay for the
context once more, but `1×` (compact, then carry a small prefix) beats `2×`
(rebuild, then keep carrying everything), so the advisor and the statusline
(`⚠ /compact first saves ~$X`) lean harder towards compacting on a cold cache.

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
| `bashOutputMaxChars` | For every Bash result longer than the cap: the carried cost of the part beyond it (Claude Code puts the rest in a file the model can still read). Tried at 16k and 8k characters against the current 30k default. |
| `effortLevel` | Not applied, only priced: if the same model ran at both `high` and `medium` in the window, the extra thinking per call at `high` × calls at `high`, billed as output (5×) plus the carried cost per thinking token observed in the same history. |

**Parallel subagent launches.** Subagents started together all send their
first request before any of them has finished writing the shared prefix
(system prompt, tools, CLAUDE.md), so every one of them writes it. `tune`
counts first calls that read nothing, estimates the prefix size as the lean
quartile of first-call sizes, and prices what launching one subagent first
and the rest after its first reply would have saved.

**Dead writes.** A cache write costs 1.25× (5m) or 2× (1h) the plain input
price. When nothing reads that entry before it expires (the last call of a
session, a call right before a long gap, or a one-shot `claude -p` prompt), the
premium was wasted. The audit sums it and names the workload it comes from.

## 8. Context diet (`kvleak diet`)

A Claude Code transcript holds every block that entered the context: tool
results (`tool_result` in user turns, mapped back to the tool call by id),
your messages, the assistant's replies, and the first prompt (system prompt,
tools, CLAUDE.md). `diet` turns each into a record with:

* **tokens** — the growth of the prompt between two calls (from `usage`, so it
  is what the API billed) split across the blocks that arrived in between, in
  proportion to their estimated size (`chars / 4` for text; images by pixel
  count, capped at 1,600, as the API prices them). The first prompt's
  unexplained remainder is the system prompt.
* **carried** — the calls that re-read it: from the call after it arrived to
  the next compaction or the end of the session.
* **carried cost** — `tokens × carried × (read + miss_rate · (write − read))`.

The assistant's reply is split into three kinds of block: **thinking**, whose
size is exact (`usage.output_tokens_details.thinking_tokens`) and which
current models (Opus 4.5+, Sonnet 4.6+, Haiku 5.5, Fable) keep in context
until a compaction; **`<Tool>` inputs**, the arguments the model sent to each
tool (a heredoc script to Bash, a whole file to Write), estimated from their
length; and **assistant text**. Exact blocks keep their size; the rest share
whatever growth remains.

**Turns.** A turn starts at a user message that is not a tool result and runs
until the next one. For every turn of 20+ calls, `diet` reports the history
re-read cost (`calls × context at the first call × r_eff`) and the subagent
counterfactual (`calls × (context − 20k) × r_eff`): what a fresh subagent with
a 20k starting context would have paid for the same loop.

`--all` sums the kinds over every interactive session in the window, keeps the
ten costliest single blocks, totals the long turns, and reports thinking per
call by model and effort level (the `effort` field on each transcript line).

**Mechanical chains.** A call is *mechanical* when every tool it asks for is
one of Bash, Read, Grep, Glob or LS, it thought for fewer than 200 tokens and
replied with fewer than 300 characters: a step a script could have taken. A
chain is a run of three or more mechanical calls inside one turn. Its price
is `(calls − 1) × context at the first call × r_eff`, the re-reads one script
would have skipped. This is an upper bound: some steps needed the previous
result before the model could choose the next one.

## 9. Hooks (`kvleak hooks`)

Claude Code hooks can replace what the model sees of a tool result
(`PostToolUse` → `updatedToolOutput`), run before and after a compaction with
the transcript path and the summary (`PreCompact`, `PostCompact`), and inject
text when a session starts after a compaction (`SessionStart` with the
`compact` matcher → `additionalContext`, capped at 10k characters). kvleak
installs four command hooks that call `kvleak hook <event>`:

| hook | input used | output |
|---|---|---|
| `PostToolUse`, matcher `Bash` | `tool_response.stdout/stderr`, `tool_use_id`, `session_id` | When stdout + stderr exceed `--max-chars` (8,000): the full text goes to `~/.cache/kvleak/outputs/<session>/<tool_use_id>.txt`, and `updatedToolOutput` carries a condensed stdout: the first 35% of the budget as head, lines matching an error pattern (plus the two lines after each, up to 20% of the budget), the rest as tail; consecutive identical lines become one line with `(×N)`; `\r` progress updates keep only the final state; a note gives the omitted line count and the path. Other fields of the response are passed through unchanged. Below the cap the hook prints nothing and the result is untouched. |
| `PreCompact` | `transcript_path`, `session_id` | Every block in the transcript (user text, assistant text, tool calls with their inputs, tool results with their full text, compaction markers) is appended to `~/.cache/kvleak/history/<session>/history.jsonl` as a numbered record; a `state.json` remembers how many records are archived so a second compaction adds only the new ones. A result whose full output was archived by the Bash hook links to that file. |
| `PostCompact` | `compact_summary` | Saved as `summary-<n>.md` next to the history. |
| `SessionStart`, matcher `compact` | `session_id`, `source` | If the session has an archive: record and result counts, the archive path, the two `kvleak recall` forms, and one line per archived user request with the record number and the tools used after it (most recent requests kept when the map exceeds 6k characters). |

`kvleak recall` reads the same files: a regex over every record's text (and
the linked full outputs) prints matching lines with one line of context under
the record header; `--show N` prints one record whole; no query prints the
request map. The hook entry point never exits non-zero and logs exceptions to
`~/.cache/kvleak/hooks.log`, so a broken hook cannot block a session.

## 10. Refine (`kvleak refine`)

`refine` turns the audit into instructions. From the 30-day `diet` aggregate
it proposes at most six rules, each gated on a number from the same history:

| rule | fires when | evidence line |
|---|---|---|
| `shell-output` | carried cost of Bash results beyond 8k chars ≥ `--min-usd` | results over 8k, cost beyond 8k |
| `scripts-in-files` | Bash inputs ≥ 12% of context and half their carried cost ≥ `--min-usd` | share, carried cost |
| `loops-in-subagents` | subagent counterfactual of long turns ≥ `--min-usd` | loops, calls, history cost, saving |
| `batch-steps` | mechanical-chain saving ≥ `--min-usd` | chains, calls, saving |
| `edit-not-write` | Write inputs ≥ 4% and half their carried cost ≥ `--min-usd` | share, largest, carried cost |
| `terse-in-loops` | assistant text ≥ 8% and half its carried cost ≥ `--min-usd` | share, carried cost |

The rules are written between `<!-- kvleak:begin -->` and `<!-- kvleak:end -->`
markers under one heading, so `--apply` replaces its own block and `--remove`
deletes only that. On `--apply`, eight behaviour metrics are stored in
`~/.cache/kvleak/refine.json` as the baseline: tokens per Bash result, the
context shares of Bash inputs, Write inputs and reply text, the share of
calls inside 20+ call loops and inside mechanical chains, calls per request,
and carried cost per call. `--probe` recomputes them over the sessions since
the apply date (at least three) and reports, per rule, the metric it targets
with the change and a verdict: moved 10% the right way, 10% the wrong way, or
no clear change. The contrast is behavioural evidence in the Harness² sense,
not a causal claim.

## Caveats

* Claude Code timestamps are written when a response finishes, not when the
  request starts. The TTL counts from the request start, so very long
  generations can make a real TTL expiry look like a `prefix_bust`.
* Dollar figures use public list prices (`kvleak models`). Discounts, batch
  pricing and subscriptions are not modelled. Treat dollars as relative
  weights. On a subscription plan, a leak costs rate-limit headroom rather than
  money.

## 11. Other tools' session stores

`readers.py` turns Codex CLI rollouts, Gemini CLI chats and OpenCode's SQLite
database into the same `Step`s. Two conventions differ from Anthropic's and
are normalised on read: OpenAI and Gemini report cached tokens *inside* the
input count (kvleak subtracts them, so `input` is always the uncached part),
and neither charges a cache-write premium (GPT-5.6 and later do, 1.25×; the
price table carries that). Thoughts and reasoning tokens are added to
`output`. Everything downstream, from miss classification to the keep-alive
budget, then works unchanged. Context-level analysis (`diet`, `tune`, hooks)
needs the tool results themselves and stays Claude Code only.

## 12. What-if (`kvleak whatif`)

For a kind of context block (shell results, all tool results, the system
prompt, replies, thinking) with total tokens `T`, entry cost `E = T × write`
and carried cost `C` (§8), a tool that promises to shrink it by `p` saves at
most `p · (E + C)`, plus `p · T × output` for the model's own output. The
bill it is compared against is the audit's input cost plus output tokens at
the output price. The `tune` row is §7's total. These are upper bounds: they
ignore the extra turns the model spends re-fetching trimmed text, which the
paired measurements in arXiv 2607.12161 found large enough to cancel the
saving.

## 13. Probe (`kvleak probe`)

`tune --apply`, `hooks --install` and `refine --apply` append `{what, at}` to
`~/.cache/kvleak/applied.json`. `probe` takes the earliest `at` (or `--at`),
keeps interactive sessions only (≥5 calls, not subagents), assigns each to
the side its first call falls on, and computes per side: input spend and
whole bill per call (§1 costs), leak share (§4), mean prompt size per call,
compactions per session (`after_compaction` flags), diet's carried cost per
call and Bash result tokens per call (§8). Change is `(after − before) /
before`. The 30-day projection is `(input per call before − after) × after
calls × 30 / after days`. Nothing is replayed; both sides are what was
billed.

**Like with like.** The 80th call of a session carries far more context than
the 5th, so a week of short sessions after `tune --apply` looks like a saving
on its own, and a long one looks like a loss. Each call is therefore also
binned by `(model, call depth: 1–10, 11–25, 26–50, 51–100, >100)` and the
change in input dollars per call is split the Oaxaca–Blinder way:

```
Ȳ_after − Ȳ_before = Σ_g w_g,before · (Y_g,after − Y_g,before)   # policy: same kind of call, new price
                   + Σ_g (w_g,after − w_g,before) · Y_g,after    # mix: the work itself shifted
```

`probe` prints both terms and projects the policy term alone
(`projected_saving_30d_adjusted_usd` in `--json`). Strata that exist on one
side only count as mix.

## 14. Skills: the listing and the bodies

Claude Code lists every available skill (name + description) in the system
prompt of every session: user skills under `~/.claude/skills`, synced skills,
skills of enabled plugins (`enabledPlugins` in settings.json) and the ones
bundled with Claude Code. `kvleak skills` reads each SKILL.md, estimates the
description at chars/4 and prices the sum as always-listed text: written at
each transcript's first call at that call's TTL write price, read at the read
price on every later call. Skills never invoked in the window get the same
pricing on their share of the listing; that is the "Unused skills off" row in
`whatif`.

When a skill is invoked its SKILL.md is injected as a meta user message
beginning `Base directory for this skill:`. `diet` classifies that block as a
skill body; `skills` prices it like any other block: a cache write on entry,
then re-read on every call until the next compaction at r_eff. A second
injection of the same skill in one session is counted as a repeat; `refine`
proposes the "skills-once" rule when repeats cost at least the gate.

Bodies are only scanned in interactive sessions and subagents; one-shot runs do
not invoke skills. The listing estimate assumes every session lists every
skill, which is true for user, synced and plugin skills and approximately true
for bundled ones.
