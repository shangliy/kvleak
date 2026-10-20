# Launch copy (English)

## Show HN

**Title:** Show HN: kvleak – my Claude Code cache hit rate was 99% and it still leaked $82

**Body:**

Prompt caching is priced asymmetrically. A cache hit costs about 0.1× the input price
(0.025× on the newest models), while a rebuild costs 1.25–2×. So one miss can cost
as much as 12–80 hits, and "92% hit rate" tells you almost nothing about your bill.

I wrote kvleak to audit the transcripts Claude Code already keeps on disk. It finds
every expensive miss, classifies why it happened (idle gap past the TTL, prefix change,
model switch, compaction, cold start, subagent cold start), and prices only the
avoidable part.

Findings from 30 days of my own use:

- 97.5% token hit rate, yet 27.5% of input spend went to cache rebuilds
- 2% of calls carried 54% of all uncached input
- 82% of the leak came from idle gaps. Claude Code now caches with the 1-hour TTL,
  so the expensive gaps are lunch, meetings and overnight. Median gap: 2.8 h.

It also has a statusline with a live TTL countdown, a keep-alive break-even
calculator (`K_max = floor((write − read) / read)`), and a playbook for multi-agent
swarms. In a simple simulation, pointers instead of pasted logs + warm workers +
one model per worker cut input cost by 86%.

`uvx kvleak`. Zero dependencies, local only, MIT. Any JSONL with token usage works,
so OpenAI- and Gemini-style logs are supported too.

https://github.com/shangliy/kvleak

## X / Twitter thread

1/ My Claude Code cache hit rate: 99.0%.
Money that session leaked to cache misses anyway: $82.

Hit rate is the wrong metric. I built a tool to measure the right one 🧵

2/ Prompt caching is asymmetric. A hit costs 0.1×, or 0.025× on Fable 5.1. A miss costs 1.25–2×.
On Fable 5.1, one miss on a 200k context = $3.95 = 80 hits.

3/ In 30 days of my own sessions, 2% of calls carried more than half of all uncached input.
82% of the leak came from walking away: lunch, meetings, overnight.

4/ `uvx kvleak` reads the transcripts Claude Code already keeps, labels every miss
(TTL, prefix bust, model switch, compaction, cold start) and puts a $ on it.

5/ Bonus: a statusline TTL countdown, a keep-alive break-even calculator, and a multi-agent playbook.
In a 6-worker swarm simulation it cuts input cost by 86%.

Zero deps, local only, MIT → https://github.com/shangliy/kvleak

## Reddit (r/ClaudeAI, r/LocalLLaMA, r/programming)

**Title:** I audited 30 days of my Claude Code sessions: 97.5% cache hit rate, 27.5% of spend still leaked to cache rebuilds

Use the HN body, and lead with the cause table from docs/benchmarks.md.
