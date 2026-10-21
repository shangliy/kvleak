# kvleak vs ccusage, rtk, caveman, claude-token-optimizer, cache-refresher

People looking to reduce Claude Code token usage usually meet these tools first. They are not
competitors of kvleak so much as answers to different questions, and kvleak's `whatif` command
prices what each one promises on *your* bill. This page is the short version; run
`kvleak whatif` for your own numbers.

| Tool | Question it answers | What it changes | What it cannot see |
|---|---|---|---|
| [ccusage](https://github.com/ryoppippi/ccusage) | How much did I spend, per day / session / model? | Nothing; a usage dashboard | Why the spend happened, or which setting would lower it |
| [rtk](https://github.com/rtk-ai/rtk) | Can shell output be smaller? | Compresses tool results before they enter the context (advertises 60–90% fewer tokens) | Carried context is billed at the cache-read price, the cheapest line of the bill, so a large token cut is a small dollar cut |
| [caveman](https://github.com/JuliusBrussee/caveman) | Can replies be shorter? | A prompt style that halves reply length (official figure 65% on output tokens) | Output is ~15% of a typical agent bill; the rest is cache writes and reads |
| [claude-token-optimizer](https://github.com/vicnaum/claude-token-optimizer) | Is the startup context too big? | Trims `CLAUDE.md`, MCP tool listings, skill listings | The per-call cost of the listing is small next to one cache rebuild of a 200k context |
| [cache-refresher](https://github.com/olddonkey/cache-refresher) | Can I keep the cache warm while I pause? | Sends keep-alive pings before the TTL expires | How many pings your own pauses justify; `kvleak keepalive` learns that from your history and feeds it |
| **kvleak** | Where does the input spend leak, and which setting stops it? | Audits the transcripts already on disk; replays your 30 days under each setting; writes the winners with an undo | Nothing live; it reads files after the fact (the statusline is the live part) |

## Why the dollar numbers come out so different

On the maintainer's 30 days (58k model calls, a $3,054 bill):

| Lever | Who promises it | Upper-bound saving | Share of bill |
|---|---|---|---|
| `kvleak tune --apply` | compaction window, TTLs, idle compaction, Bash cap | −$807 | 26% |
| Shell output −60% | rtk's low end | −$216 | 7% |
| All tool results −40% | AgentDiet-style trimming | −$180 | 6% |
| Unused skills off | `kvleak skills` | −$161 | 5% |
| Thinking −25% | effort high → medium | −$59 | 2% |
| Startup context −50% | claude-token-optimizer | −$42 | 1% |
| Replies half as long | caveman | −$33 | 1% |

Tool-output compressors shrink what the context *carries*, and carrying is paid at the
cache-read price. Compaction and TTL settings change whether a 200k prefix is rebuilt at all,
and one rebuild costs as much as 80 hits. That is the whole reason the first row is larger than
the rest combined. On 2,848 billed Claude Code runs, cutting tool output by 38% raised cost 6.8%
([arXiv 2607.12161](https://arxiv.org/abs/2607.12161)).

## Use them together

- ccusage for the total, kvleak for the cause and the fix.
- rtk or `kvleak hooks --install` for long shell output (kvleak's hook keeps head, tail, error
  lines and a path to the full text; `kvleak recall` finds it later).
- cache-refresher for the pings, `kvleak keepalive` for how many are worth sending.
- caveman if you like the style; `kvleak diet` shows what share of your context is replies.

Reproduction commands for every number: [benchmarks.md](benchmarks.md).
