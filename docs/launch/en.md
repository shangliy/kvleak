# Launch copy (English)

Numbers match the README audit block (maintainer's 30 days, 2026-09-21 to 2026-10-20)
and docs/benchmarks.md. Assets: docs/assets/demo.gif (audit → whatif → tune),
docs/assets/report.png.

## Show HN

**Title (79 chars):** Show HN: kvleak – my Claude Code cache hit rate was 95%, a fifth of input spend still leaked

Alternative: Show HN: kvleak – "60–90% fewer tokens" did not shrink my Claude Code bill, four settings did

**URL:** https://github.com/shangliy/kvleak

**Text:**

Prompt caching is priced asymmetrically: a cache hit costs 0.1× the input price
(0.025× on the newest models), a rebuild 1.25–2×. One miss on a 200k context is
worth about 80 hits, so a 95% hit rate can hide a lot of money.

kvleak reads the session files Claude Code (and Codex CLI, Gemini CLI, OpenCode)
already keep on disk, labels every miss (idle gap past the TTL, prefix change,
model switch, compaction, cold start, subagent cold start) and prices only the
avoidable part. No API key, no network, zero dependencies: `uvx kvleak`.

On my own 30 days (101 sessions, 58k model calls):

- 95.4% hit rate, yet 20.7% of input spend went to cache rebuilds
- 2% of calls carried more than half of all uncached input
- 69% of the leak was idle gaps: lunch, meetings, overnight (the 1h TTL)
- 44% of the whole bill was cache *writes*, not reads or output

The thing I did not expect: tool-output compressors (rtk and friends) shrink
what the context carries, and carrying is paid at the cache-read price, the
cheapest line on the bill. A recent study of 2,848 billed Claude Code runs
found a 38% cut in tool output raised cost 6.8% (arXiv 2607.12161). `kvleak
whatif` replays each tool's promise on your own bill as an upper bound; on mine
rtk's low end is worth 7% and four Claude Code settings are worth 26%.

Those settings exist already (auto-compact window, cache TTLs, idle compaction,
Bash output cap), just with defaults tuned for nobody. `kvleak tune` replays
your last 30 days under every value and `--apply` writes the winners: −45% of
input spend on mine, mostly from compacting at 200k instead of ~967k. A 72-run
A/B benchmark on Opus 5.5 scored 1.0 in every arm, so no accuracy cost that I
could measure. A week after applying, `kvleak probe` contrasts your own
sessions before vs after, measured on what was billed rather than replayed.

Also in the box: a statusline with a live TTL countdown, hooks that keep long
shell output out of the context and make compaction lossless, and a keep-alive
budget learned from when you actually come back from pauses.

I would like other people's numbers; the audit is three seconds and local.
There is an issue for that: https://github.com/shangliy/kvleak/issues/1

## Anticipated HN questions (paste-ready answers)

**"I'm on a Max subscription, dollars don't matter."** The misses eat the same
rate-limit headroom they would cost on the API. Read the figures as relative
weights; a 20% leak is 20% of your five-hour window.

**"Doesn't compacting at 200k make the model dumber?"** It only compacts above
200k, which is where Claude Code's own 1M-context models start degrading
anyway. The benchmark (bench/, 72 runs) found no accuracy change; `--min-window
300k` if you want more headroom. `tune --reset` undoes it.

**"Why not just use rtk / caveman?"** They are compatible, and `whatif` will tell
you what each is worth on your bill. On mine the compressor is 7% and the
compaction window is 26%, because 44% of my bill is cache writes, which
compressors do not touch.

**"Is my data sent anywhere?"** No. Local files only. The code is 5k lines of
stdlib Python; grep for `socket` or `urllib` and you will find nothing.

**"Does it work with Cursor / Aider / Codex?"** Codex CLI, Gemini CLI and
OpenCode session stores are read natively for the audit, keepalive and whatif.
Any JSONL with usage counters works via `--path`. The context-level commands
(tune, hooks, diet, refine) need the tool results themselves and are Claude Code
only for now.

## Reddit

**r/ClaudeAI title:** I audited 30 days of my Claude Code sessions: 95% cache hit rate, 20% of input spend still leaked to cache rebuilds. Four settings Claude Code already has fixed most of it.

**r/LocalLLaMA / r/programming title:** "60–90% fewer tokens" did not shrink my Claude Code bill. Here is what did (with a tool to replay it on yours).

**Body:** the Show HN text above, with docs/assets/demo.gif attached and the
`kvleak whatif` table pasted as a code block. End with the four-command "Turn it
on" block from the README.

## X / Twitter launch thread

1/ My Claude Code cache hit rate over 30 days: 95.4%.
Input spend that still went to cache rebuilds: $538, a fifth of it.

Hit rate counts tokens. Your bill counts misses × context size.

I built a tool that shows the real number. `uvx kvleak` 🧵 [attach demo.gif]

2/ Prompt caching is asymmetric. A hit is 0.1× (0.025× on Fable 5.1). A rebuild is 1.25–2×.

One miss on a 200k context = 80 hits. 2% of my calls carried more than half of all uncached input.

3/ 69% of the leak was walking away. Claude Code caches for 1h now, so the expensive gaps are lunch, meetings, overnight.

Idle 84 min → $8.92 rebuild. One message at minute 59 → $0.11.

4/ The surprise: "60–90% fewer tokens" tools shrink what the context *carries*, paid at cache-read price, the cheapest line.

2,848 billed runs: −38% tool output → +6.8% cost (arXiv 2607.12161).

`kvleak whatif` replays each tool's promise on your bill. Mine: rtk 7%, four settings 26%.

5/ Those four settings are already in Claude Code (compaction window, TTLs, idle compaction, Bash cap), tuned for nobody.

`kvleak tune` replays your 30 days under every value. `--apply` writes the winners. −45% input spend on mine. 72-run benchmark: accuracy 1.0 in every arm.

6/ Zero deps, no API key, no network, reads Claude Code + Codex CLI + Gemini CLI + OpenCode.

`uvx kvleak` takes 3 seconds. Post your numbers: github.com/shangliy/kvleak/issues/1
