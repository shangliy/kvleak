# Reddit posts (paste-ready)

Post one subreddit per day. Attach the video (`~/Downloads/kvleak-demo.mp4`) as a video post on
r/ClaudeAI; on text-only subs link the release asset. Reply to every comment within the first two
hours; the FAQ answers are in `en.md`.

## r/ClaudeAI (day 1)

**Title:** I audited 30 days of my Claude Code sessions: 95% cache hit rate, 20% of input spend still leaked to cache rebuilds. Four settings Claude Code already has fixed most of it.

**Flair:** Productivity (or "Coding" if Productivity is not offered)

**Body:**

Prompt caching is priced asymmetrically: a cache hit costs 0.1× the input price (0.025× on the newest models), a rebuild 1.25–2×. One miss on a 200k context is worth about 80 hits, so a 95% hit rate can hide a lot of money.

I wrote a small tool, **kvleak**, that reads the session files Claude Code already keeps in `~/.claude/projects`, labels every miss (idle gap past the TTL, prefix change, model switch, compaction, cold start, subagent cold start) and prices only the avoidable part. No API key, no network, zero dependencies:

    uvx kvleak

On my own 30 days (101 sessions, 58k model calls):

- 95.4% hit rate, yet **20.7% of input spend** went to cache rebuilds
- 2% of calls carried more than half of all uncached input
- 69% of the leak was idle gaps: lunch, meetings, overnight. 97% of that came from gaps over an hour, coming back to a 150k–560k context
- it is concentrated: my top 5 sessions were 51% of the leak, 14 marathon sessions (200+ turns) were 56%
- 44% of the whole bill was cache **writes**, not reads or output

**The part I did not expect.** Tool-output compressors (rtk and friends) shrink what the context carries, and carrying is paid at the cache-read price, the cheapest line on the bill. A study of 2,848 billed Claude Code runs found a 38% cut in tool output *raised* cost 6.8% (arXiv 2607.12161). `kvleak whatif` replays each tool's advertised number on your own bill as an upper bound:

    if you…                who promises it                                     bill saving  share of bill
    kvleak tune --apply    compaction window, TTLs, idle compaction, Bash cap  −$807.14             26.3%
    Shell output −60%      rtk's low end ("60–90%")                            −$216.35              7.1%
    All tool results −40%  AgentDiet, arXiv 2509.23586                         −$180.47              5.9%
    Unused skills off      kvleak skills: 12 of 19 never invoked               −$161.21              5.3%
    Thinking −25%          effort high → medium                                −$59.34               1.9%
    Startup context −50%   claude-token-optimizer                              −$41.80               1.4%
    Replies half as long   caveman                                             −$33.36               1.1%

**What actually moved the bill** were four settings Claude Code already has (auto-compact window, cache TTL for `claude -p`, idle compaction, Bash output cap), just with defaults tuned for nobody. `kvleak tune` replays your last 30 days under every candidate value and `--apply` writes the winners to `settings.json` with a backup. On mine: −45% of input spend, mostly from compacting at 200k instead of ~967k. A 72-run A/B benchmark on Opus 5.5 scored 1.0 in every arm, so no accuracy cost I could measure. A week later `kvleak probe` contrasts your own sessions before vs after, on what was actually billed.

Turn it on (each line has an undo flag):

    pip install kvleak
    kvleak tune --apply          # settings.json: compaction window, cache TTLs, idle compaction, Bash cap   (--reset)
    kvleak hooks --install       # long shell output stays out of the context; compaction keeps an archive  (--uninstall)
    kvleak refine --apply        # context-hygiene rules, with their dollar figures, in ~/.claude/CLAUDE.md (--remove)
    kvleak statusline --install  # cache countdown in the status bar, ticking while you read              (--uninstall)

On a Max/Pro subscription the dollars are list-price equivalents; the percentages are your rate-limit headroom, which is the thing you actually feel.

Repo (MIT): https://github.com/shangliy/kvleak
Two-minute video: https://github.com/shangliy/kvleak/releases/download/v0.9.1/kvleak-demo.mp4

I would really like other people's numbers. The audit is three seconds and local; there is an issue for posting yours: https://github.com/shangliy/kvleak/issues/1

## r/ChatGPTCoding (day 2)

**Title:** "60–90% fewer tokens" did not shrink my Claude Code bill. Here is what did, with a tool that replays it on yours.

**Body:** same as above, but open with the `whatif` table and the arXiv finding, then the audit numbers, then the four commands. Mention in the first paragraph that it also reads Codex CLI, Gemini CLI and OpenCode sessions.

## r/LocalLLaMA (day 3)

**Title:** Prompt-cache economics for agent loops: one miss on a 200k context = 80 hits. I measured 30 days of Claude Code and Codex CLI sessions.

**Body:** lead with the pricing asymmetry and the `K_max = floor((write − read) / read)` keep-alive bound, then the audit table and the idle-gap distribution (97% of the idle leak from gaps over an hour). Keep the commands to `uvx kvleak`; this sub cares about the mechanism more than the installer.
