# Launch copy (English)

Numbers match the README audit block (maintainer's 30 days, 2026-09-21 to 2026-10-20)
and docs/benchmarks.md.

Assets:
- **Explainer video, 2:23, 1080p with audio** (hit rate lies → see the leak → fix the
  settings → before/after → benchmark → four commands → probe):
  https://github.com/shangliy/kvleak/releases/download/v0.9.1/kvleak-demo.mp4
  X-safe cut (≤2:20 for non-Premium accounts): `~/Downloads/kvleak-demo-x-140s.mp4`
- Terminal GIF (audit → whatif → tune, 35 s): docs/assets/demo.gif
- Thumbnails: `~/Downloads/kvleak-thumb-leak.png` ($537.72 slide),
  `~/Downloads/kvleak-thumb-before-after.png` ($3,053 → $2,266 slide)
- docs/assets/report.png

Where each goes: X thread tweet 1 and Reddit posts carry the video; HN gets the repo
URL and a "2-minute video" line in the text; the README keeps the GIF (GitHub does
not inline-play release assets) with a link to the video under it.

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

Two-minute video of the whole argument, numbers and commands:
https://github.com/shangliy/kvleak/releases/download/v0.9.1/kvleak-demo.mp4

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

**Body:** the Show HN text above. Upload the video as a Reddit video post
(r/ClaudeAI allows it; for text-only subs link the release asset), paste the
`kvleak whatif` table as a code block, end with the four-command "Turn it on"
block from the README.

## X / Twitter launch thread

Tweet 1 carries the video (`~/Downloads/kvleak-demo-x-140s.mp4`, under the 2:20
limit). X autoplays muted, which is fine: every number in the video is on screen.
The thread follows the video's chapters so a reader who stops at any tweet has
seen the same argument.

1/ My Claude Code cache hit rate over 30 days: 95.4%. Looks healthy.

Input spend that still went to cache rebuilds: $537.72. A fifth of it.

Hit rate counts tokens. Your bill counts misses × context size. 2 minutes on why, and the fix 🧵 [attach video]

2/ Why the hit rate lies: a cache hit is 0.1× the input price (0.025× on Fable 5.1). A rebuild is 1.25–2×.

One miss on a 200k context = 80 hits.

2.2% of my calls were heavy misses. They carried 53.7% of all uncached input. The hit rate never moved.

3/ See the leak: `uvx kvleak`. No API key, no config, no network. It reads the session files Claude Code already keeps.

#1 cause on my machine: idle gaps past the TTL, 69% of the leak. Lunch, meetings, overnight.

4/ Fix the settings: Claude Code already has them (compaction window, cache TTLs, idle compaction, Bash output cap). Defaults tuned for nobody.

`kvleak tune` replays your 30 days under every value. `--apply` writes the winners. Mine: $909 / 30 days, −45% of input spend.

5/ Before vs after, whole bill: $3,053 → $2,266 per 30 days, −26%.

And the accuracy check: 72 benchmark runs, 4 arms, every run scored 1.00.

(What "60–90% fewer tokens" tools do to the same bill: −7%. Carrying is paid at cache-read price, the cheapest line. `kvleak whatif` shows yours.)

6/ Four commands, each with an undo:

kvleak tune --apply
kvleak hooks --install
kvleak refine --apply
kvleak statusline --install

A week later `kvleak probe` shows your own before/after, measured on what was billed.

7/ Zero deps, MIT, reads Claude Code + Codex CLI + Gemini CLI + OpenCode.

`uvx kvleak` takes 3 seconds. Post your numbers: github.com/shangliy/kvleak/issues/1
