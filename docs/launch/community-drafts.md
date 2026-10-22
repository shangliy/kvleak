# Reddit and V2EX launch drafts

Date: 2026-10-21
POC: repository owner
TL;DR: Ready-to-post drafts for r/ClaudeAI, r/LocalLLaMA and V2EX. Post them from your own logged-in account; post on different days, as plan.zh.md says. Don't mention `--share` until a release with it is on PyPI.

## r/ClaudeAI (video post, weekday 9:00 PT)

Title: I audited 30 days of my Claude Code sessions: 95% cache hit rate, 20% of input spend still leaked to cache rebuilds. Four settings Claude Code already has fixed most of it.

Body:

Cache hits cost 0.1x the input price and rebuilds cost 1.25-2x, so one miss on a 200k context costs about as much as 80 hits. A 95% hit rate can still hide a lot of spend.

I wrote kvleak, which reads the session files Claude Code already keeps on disk and prices each miss by cause. It runs locally with no API key and no dependencies: `uvx kvleak`.

On my last 30 days:

- 95.4% hit rate, but 20.7% of input spend went to cache rebuilds
- 2% of calls carried more than half of all uncached input
- idle gaps past the TTL (lunch, meetings, overnight) were 69% of the leak

Claude Code already exposes the settings that matter: auto-compact window, cache TTLs, idle compaction and the Bash output cap. `kvleak tune` replays your own history under each value. On mine the replay estimated -45% input spend. That's an estimate; `kvleak probe` measures the real before/after a week later.

I also ran it on a public 945-run benchmark (github.com/kenseii1/claude-code-limits-tests). Priced with the costUSD in those logs, /compact cut raw tokens 39-60% but cost 14-36% more over the next 3 questions, and only broke even after about 7-8 questions.

Repo: https://github.com/shangliy/kvleak
If you run it, I'd like to see your numbers in issue #1.

## r/LocalLLaMA (text post, a different day)

Title: "60-90% fewer tokens" didn't shrink my Claude Code bill. Raw tokens and dollars disagree because cache reads are 10x cheaper than writes.

Body: lead with the token-vs-dollar point. Include the costUSD table from docs/launch/kensei-945-analysis.md (section 1) and the subagent table (section 2), credit Kensei's dataset, then give `uvx kvleak` and the repo link. Say it reads Claude Code, Codex CLI, Gemini CLI and OpenCode logs, and that audit/whatif/keepalive also accept any JSONL with usage counters via `--path`.

## V2EX (分享创造)

标题：开源了一个工具：Claude Code 缓存命中率 95%，为什么还有五分之一的输入花费在缓存重建上？

正文：按 docs/launch/zh.md 的 V2EX 结构写，并补一段：

> 我用它跑了 Kensei 公开的 945 次 Claude Code 实验日志。按原始 token 算，/compact 之后 3 个问题省 39–60%；按日志里自带的 costUSD 算，反而贵 14–36%，要 7–8 个问题之后才回本。原因是缓存读取只收 0.1 倍，而压缩要付输出和重新写缓存的钱。

结尾：`uvx kvleak`，仓库链接，欢迎把数字贴到 issue #1。
