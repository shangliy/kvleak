# The multi-agent cache playbook

A single agent session usually runs at a 90%+ prompt-cache hit rate and still
leaks a lot of money, because the few misses are huge. When you add more agents
and more models, three effects multiply the leak. This page explains those
effects and six layers of practice that remove most of the leak. `kvleak` measures
each one.

## Why the leak is concentrated

Prompt caching is priced asymmetrically. For a cached prefix of `N` tokens:

| what happens on the next call | cost (in base input tokens) |
|---|---|
| cache hit (the read also resets the TTL for free) | `0.10 × N` (0.05× / 0.025× on some newer models) |
| cache expired or busted, so it is rebuilt | `1.25 × N` (5-minute TTL) or `2.00 × N` (1-hour TTL) |

One miss costs about as much as 12 hits. In a typical audit, about 5% of calls
are heavy misses, and they account for about half of all uncached input. Most
of those misses fall into two groups:

1. **TTL expiry.** You stepped away (reading code, waiting on a build or a
   meeting) for longer than the TTL, so the next message rebuilds everything.
2. **Prefix busts.** Within the TTL, something in the prefix changed: a
   timestamp, unsorted JSON, a different tool list, or a different model.

## Three ways multi-agent setups multiply the leak

**1. Caches don't cross models.** KV state belongs to the weights that produced
it. Two models cannot share a cache, even for the same text. Moving a 130k
context from model A to model B, or flipping models mid-session, always means
a full-price rebuild. `kvleak` reports this as `model_switch`.

**2. A shared board costs O(N²).** If one worker pastes a 500-line build log
onto a board that six workers read, all six carry that log in their context.
They pay to read it on every later turn, and they pay to rebuild it whenever
their caches expire.

**3. One-shot subagents pay a cold-start tax.** Every new agent pays full
price to prefill its system prompt, tool schemas and skills, often 20–30k tokens
before it does anything. If you spawn agents for routine retries, you pay that
tax every time. `kvleak` reports this as `subagent_cold_start`.

## Six layers of practice

| # | Layer | What to do | Why it saves money | kvleak signal |
|---|---|---|---|---|
| 1 | **Pin one model per worker, hand off with pointers** | Bind each worker to one model for its whole life. To hand work to a different model, write the big content to a file. Pass only `{path, line range, sha256, a summary of 200 tokens or less}` (`kvleak pointer`). | Each worker keeps its own cache warm, and the cross-model hand-off costs about 100 tokens instead of the full context. | `model_switch` |
| 2 | **Compute funnel: script, then small model, then large model** | Use plain code for anything deterministic: log parsing, regex lint, AST checks, metric deltas. That costs 0 tokens and leaves no trace in any cache. Give broad search and triage to a small, fast model. Keep the large model for design and fixes. | Megabytes of logs become 2 KB of JSON before any model sees them. | lower `uncached` overall |
| 3 | **Keep workers warm and batch work inside the TTL** | Keep workers idle instead of killing and respawning them. An idle worker costs nothing. Wake an existing worker with a message. Run builds and tests in the background, then wake the agent once and let it close the loop inside one TTL window. If you must wait on a large context, cap keep-alive pings at the break-even point (`kvleak breakeven`). | Skips the cold-start tax and reuses cached code context. | `ttl_expiry`, `subagent_cold_start` |
| 4 | **Slim board contract (15 lines or fewer)** | Never paste raw logs, full diffs or whole files onto a shared board. Post only `status`, `change_id`, `metric_delta` and `log_path`. Only the worker that owns the fix opens the log. | Turns O(N²) broadcast into O(1). | smaller `rebuilt` per miss |
| 5 | **Keep the prefix deterministic** | Keep system prompts and role briefs byte-identical. Put no timestamps or mutable state in the header. Serialize with `json.dumps(..., sort_keys=True)` and keep tool lists in a stable order. | Prefix caching matches token by token, so one changed byte near the top invalidates everything after it. | `prefix_bust` |
| 6 | **Compact at the right moment and distil offline** | Compact once at a natural break, for example when uncompacted history passes about 250 KB (~60k tokens). Don't let auto-compact fire in the middle of a task. For long experiments, distil the history ledgers into a one-page table of what worked and what didn't. | A compaction is a forced full rebuild. Do it once, on purpose. | `compaction` |

## A contract you can paste into your swarm

```markdown
### Token & cache efficiency contract

- **Pinned models, warm workers.** Never switch a worker's model mid-conversation.
  Never kill and re-add workers for routine retries. Idle workers cost nothing;
  wake them with a message.
- **Slim board (15 lines or fewer), pointers only.** Never paste raw logs, full diffs,
  or full files onto the shared board. Write them to disk and post
  `{status, change_id, metric_delta, file_path}`.
- **TTL batching with a break-even guard.** Batch interactions with one worker
  inside the cache TTL. When waiting on long builds or tests, cap keep-alive
  pings below break-even (≤ 5 recommended; never more than `kvleak breakeven` says).
- **Deterministic prefixes.** Role briefs are static. JSON is emitted with
  sorted keys. No timestamps above the last cache breakpoint.
```

## The break-even rule for keep-alive pings

Letting a prefix of `N` tokens expire costs an extra `(write − read) × N`. A
keep-alive ping costs `read × N` and resets the TTL. So keep-alive pays off for
at most:

```
K_max = floor((write − read) / read)
      = 11 pings for 0.10× reads (≈ 55 min at a 5-minute TTL)
      = 24 pings for 0.05× reads
      = 49 pings for 0.025× reads
```

Beyond `K_max`, let the cache die. `kvleak breakeven 150k --idle 20m` does this
math for you. In Claude Code, `kvleak statusline` shows a countdown, so a
well-timed message of your own does the same job as a ping for free.

A good keep-alive is the previous request re-sent with `max_tokens: 0` (or
`max_tokens: 1` where 0 isn't accepted), shortly before expiry. It bills only a
cache read.
