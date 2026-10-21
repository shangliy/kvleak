# kvleak 完整指南

[README](../README.zh-CN.md) 没讲的全在这里：每个命令、背后的数字、怎么测出来的。English: [guide.md](guide.md)。

## 真实数据

- 30 天交互式 Claude Code 会话：命中率 **97.5%**，但 **27.5%** 的输入花费用在了缓存重建上
- 其中 **82%** 的漏损来自空闲间隔（中位数 2.8 小时；Claude Code 现在默认使用 1 小时 TTL）
- 200k 上下文一次未命中的成本：Fable 5.1 上 **$3.95**，Opus 5.5 上 $1.56
- 6 个 Worker 的 Swarm 模拟：指针交接 + 常驻 Worker + 锁定模型，输入成本 **−86%**
- 压缩顾问回测：只在上下文超过 200k 时压缩（平均每个会话约 3 次），输入花费 **−58%**；只在超过 400k 时压缩（30 天共 23 次）也有 **−40%**
- `kvleak tune --apply`：同样的 30 天，四个 Claude Code 自带的设置合计 **−$909（−45%）**
- 机械链（连续 3 次以上 `ls`/`cat`/`grep`/小读取、中间没有真正的决策）：**493 条，占 9,924 次调用中的 2,235 次**；合成脚本最多 **−$160**
- `kvleak refine`：5 条写进 CLAUDE.md 的规则，每条都附带证据，合计覆盖 **≈$640 / 30 天** 的可测量浪费
- A/B benchmark（`bench/`，Opus 5.5 上 72 次 `claude -p`）：**每个 arm 准确度都是 1.00**；输出 1.7 万行和 13 万行的两个任务成本 −16% 到 −17%，四个短任务在噪音范围内（±5%）。大头要在长会话里才能省
- `kvleak keepalive`：同样的 30 天里 323 次停顿超过了 1 小时 TTL，164 次再也没回来；"最多 ping 5 次再放弃"本可省 **$337**（最优 14 次、$413）。午饭时开始的停顿只有 25% 会回来，早上 6–8 点开始的有 86%

## 功能

| 命令 | 作用 |
|---|---|
| `kvleak` | 全量审计：命中率、漏损金额、按原因拆解、最漏的会话、最贵的单次未命中、可执行建议 |
| `kvleak --since 7d --html report.html` | 生成可分享的自包含 HTML 报告（亦支持 `--markdown` / `--json`） |
| `kvleak session <id>` | 单个会话逐步时间线，标出每一次未命中及原因 |
| `kvleak statusline --install` | 在 Claude Code 状态栏显示**缓存 TTL 倒计时**：`◕ cache 3:41 · 182k ctx · rebuild $0.89`。Claude Code 本身只在有事件时重跑状态栏，所以安装时会写入 `refreshInterval: 5` 让它空闲时也每 5 秒走一格（`--refresh 1` 每秒走，`--refresh 0` 只跟事件）。装完后**新开**的会话、第一条回复之后才会出现 |
| `kvleak advise` | 告诉你**现在该不该 `/compact`，或者写一份交接文档换新会话**，以及几次调用后回本；`--backtest` 用你的历史数据回测 |
| `kvleak tune --apply` | 用你的历史回测 Claude Code 自带的缓存设置（自动压缩窗口、缓存 TTL、空闲压缩阈值），把最省钱的值**直接写进 `settings.json`** |
| `kvleak diet` | 拆开上下文：**到底是什么把它填满的**（工具输出、你贴的文本、模型自己的回复、系统提示词），每一块被携带了多少次、花了多少钱，并点名最贵的单项；`--all` 汇总 30 天 |
| `kvleak hooks --install` | 装 4 个 Claude Code hook：**长 shell 输出不进上下文**（模型只看到头、尾、报错行和完整输出的路径），**压缩不再丢东西**（被压掉的全部归档，并告诉模型怎么查） |
| `kvleak recall "<regex>"` | 搜索被压缩掉的历史。模型自己也会用它 |
| `kvleak refine --apply` | 把**有证据的上下文卫生规则**写进 `CLAUDE.md`，每条规则都要在你自己的历史上值一个金额才会出现；之后 `--probe` 对比前后，告诉你哪条规则真的改变了行为 |
| `kvleak keepalive` | 从你自己的停顿记录里学出**保活 ping 几次才划算**（整体和分时段），倒计时最后两分钟 statusline 会显示；把这个预算交给刷新工具（cache-refresher 的 `/cache-auto on <k>`） |
| `kvleak breakeven 150k --idle 20m` | 单次计划停顿：保活还是放弃？按盈亏平衡点 `K_max = floor((write − read) / read)` 计算 |
| `kvleak pointer build.log --status FAIL` | 把大文件变成约 60 Token 的「指针消息」，用于多 Agent 看板交接 |
| `kvleak --fail-over 15` | 漏损超过输入花费的 15% 时以退出码 2 结束，可作为 CI 门禁 |

## 上下文太大，什么时候该压缩？

有缓存，长上下文也不是免费的：每次调用都要按读取价把整段前缀读一遍，一旦未命中还要全量重建。压缩只花一次钱，之后每次调用都更便宜：

```
回本调用次数 R* = 一次性成本 / ((C − S) · (read + 未命中率 · (write − read)))
```

`kvleak advise` 会用你的历史数据估计会话通常还会持续多少次调用、多久未命中一次，然后告诉你现在压缩或换新会话是否划算。状态栏也会实时提示：`⚠ /compact saves ~$9.42`。这些结论只算钱：请在摘要仍能保留后续所需信息时再压缩。

## 让 Claude Code 自己来：`kvleak tune`

Claude Code 其实能替你压缩（`autoCompactWindow`）、选缓存 TTL（`promptCacheTtl`、`subagentPromptCacheTtl`），
还能在缓存失效前把空闲会话压缩掉（`CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS`）。但默认值不是为你调的。
`kvleak tune` 用**你自己**最近 30 天的记录，把每个设置的每个候选值都回放一遍，告诉你各能省多少：

```text
$ kvleak tune

  setting                                         now           best   saving / 30d  why
  autoCompactWindow                               auto (~967k)  200k        $720.52  98 compactions over 33 sessions instead of 32 · −57.7% of input spend
  promptCacheTtl                                  1h            1h                –  33 interactive sessions: 1h costs $1,347, 5m costs $3,231
  subagentPromptCacheTtl                          5m            5m                –  271 subagents: 5m costs $274, 1h costs $386
  `claude -p` runs: CLAUDE_CODE_PROMPT_CACHE_TTL  1h            5m          $169.11  20535 one-shot runs: 5m costs $301, 1h costs $471 · set it in the batch script
  CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS             200k          100k         $19.72  153 idle gaps ≥54 min · compacting there nets $212 at 200k, $232 at 100k

  Total if applied: $740.43 per 30d  (+$169.11 from the habits above)
  Write these to ~/.claude/settings.json: kvleak tune --apply  (undo: kvleak tune --reset)
```

`--apply` 会先备份 `settings.json`，`--reset` 只删掉它写入的那几个键。想保留更多细节可以用
`--min-window 300k` 限制自动压缩的下限。审计报告里还新增了**死写入**一项：按 1 小时 TTL
的 2 倍写入价缓存、却从来没被读回的 Token。对一次性的 `claude -p` 批处理来说这占了账单的 42%，
所以那类任务该用 5 分钟 TTL。

## 上下文到底是被什么填满的？`kvleak diet`

压缩把上下文当成一整块。`kvleak diet` 把它拆开：每个工具输出、每段贴进来的文本、每条回复，都是在某次调用进入上下文、
之后每次调用都被重读、直到被压缩掉的一块。Token 数按 API 的真实用量校准（图片按像素计价，不按 base64 长度），
每一块都有一个**携带成本** = Token × 停留的调用次数 × 缓存读取价。

```text
$ kvleak diet --all

  What filled the context · 33 sessions · 9,824 calls · 20.8M tokens entered · carrying it cost $958
  source                 items  tokens  share  biggest  carried cost
  Bash results           7,760    6.1M  29.2%    15.9k       $297.87
  Bash inputs            7,761    4.5M  21.5%    10.2k       $205.07
  Thinking               6,472    3.0M  14.2%    12.4k       $140.21
  Your messages          1,891    2.0M   9.6%    49.7k        $82.10
  System prompt & tools     33    1.3M   6.3%    61.6k        $61.91
  Write inputs             349    999k   4.8%    27.1k        $46.95
  Read results             421    925k   4.4%    25.2k        $38.88

  Long turns (≥20 calls before you spoke again)
  114 turns · 4,707 calls · re-reading the history they started with cost $351.12 · in fresh subagents: −$318.66
```

两个意外。一半的上下文是 **shell**：从 shell 回来的输出（29%）和模型发给 shell 的脚本（21%），两者都有便宜的解法
（`tail`、脚本写进文件而不是 heredoc、`bashOutputMaxChars`——`kvleak tune` 现在会回测它）。
另外，当前模型会把**思考一直留在上下文里直到压缩**，所以它占了重读量的 14%；但在这份历史上把 `/effort` 从 high 降到 medium 只能省 $3，
真正的杠杆是压缩而不是 effort。最大的单项数字是**长回合**：114 个超过 20 次调用的工具循环，把它们开始时的历史重读了 4,707 次。
把这类循环放进新的子 Agent 里跑，$319 就省下来了。

`diet` 还会数**机械链**：连续 3 次以上的调用，每次只做一个 `ls`、`cat`、`grep`、`find` 或小段读取，中间没有真正的决策
（思考不到 200 token、回复不到 300 字符）。每一步都把整个上下文重读一遍，而一段脚本只读一次。同一份历史上：
493 条链，2,235 / 9,924 次调用，合成脚本最多省 $160。**账单是按调用次数算的，不是按 token。**

## 技能：每次调用都在付的列表，和调用后一直带着的正文

Claude Code 能看到的每个技能（`~/.claude/skills`、同步技能、启用的插件、自带技能）的名字和描述都在每个会话的系统提示里。
这是缓存前缀的一部分：每次会话启动写一次，之后每次调用读一次，不管有没有人调用它。技能一旦被调用，整份 SKILL.md
注入上下文，一直带到下次压缩；再调一次就再注入一次。

```text
$ kvleak skills

  kvleak skills · 19 skills listed · 24,780 sessions · 40,258 model calls over the last 30d
  Listed in every call: 3.3k tokens of skill descriptions ≈ $258.80 / 30d
  Loaded bodies: 11 injections carried through their sessions ≈ $5.02

  Never invoked in the window: 12 skills, 1.9k tokens of listing ≈ $152.75 / 30d
    synced         built-in-browser, chrome-browser, computer-use, …  → unsync it in claude.ai → Settings → Skills
    user           find-skills, glowmotion, llm-journey  → move the folder out of ~/.claude/skills
```

对大多数人列表是更大的那笔：单次很小，但每次调用都读、每次会话启动都写，包括每一次一次性的 `claude -p`。
正文的花费在 `diet` 里以 "Skill bodies" 一类显示；对应的杠杆是渐进披露（SKILL.md 保持短，参考资料放到技能按需读的文件里）、
把大技能放到子 Agent 里加载、不重复调用已在上下文中的技能（重复注入花得够多时 `refine` 会提出这条规则）。
`whatif` 里对应 "Unused skills off" 一行。`--json` 给脚本用。

## 从 REPL 型 harness 借来的三件事：hooks、recall、refine

给模型一个常驻 REPL 而不是工具列表的 harness（[Prime Agent](https://github.com/PrimeIntellect-ai/prime-agent)）把工具输出
放在**变量**里：模型只打印它需要的部分，其余不进 prompt，被压缩掉的历史也仍然可以取回。Harness 自改进框架
（[Harness²](https://github.com/google-research/harness2)）修改模型周围的文件，并且只保留有证据支持的修改。
Claude Code 的工具模型改不了，但它的 hooks 和 `CLAUDE.md` 足够拿到这两者的大部分好处。

```bash
kvleak hooks --install      # 往 ~/.claude/settings.json 写 4 个 hook（撤销：--uninstall）
```

| hook | 做什么 |
|---|---|
| `PostToolUse`（Bash） | 超过 8,000 字符（`--max-chars`）的结果写进 `~/.cache/kvleak/outputs/<session>/<id>.txt`。模型看到的是开头、结尾、所有像报错的行（连同后两行）、重复行折叠成 `(×N)`，以及完整输出的路径。什么都没丢，只隔一个 `grep` |
| `PreCompact` | 摘要替换历史之前，把记录里每一个工具结果、消息、回复追加到 `~/.cache/kvleak/history/<session>/history.jsonl` |
| `PostCompact` | 摘要本身也存在旁边，方便看它保留了什么 |
| `SessionStart`（compact） | 告诉模型归档存在，给它一行一个请求的地图和 `kvleak recall` 命令。压缩后它做的第一件事不再是"重新跑一遍测试看看哪里失败" |

```text
$ kvleak recall "AssertionError" --session 7ff95ce0
#312 result Bash pytest -q tests/test_policy.py
    > FAILED tests/test_policy.py::test_window_sweep - AssertionError: assert 98 == 96
$ kvleak recall --show 312 | head -40
```

`kvleak refine` 是"持续 harness"的那一半。它拿 `diet` 和 `tune` 测出来的数字，给 `~/.claude/CLAUDE.md`
（`--local` 则是 `./CLAUDE.md`）生成一小段规则。一条规则只有在它的证据在时间窗内值 `--min-usd`（默认 $5）以上时才出现，
而且证据会写进规则里，让模型知道为什么：

```text
$ kvleak refine

  5 rules with evidence from 33 sessions · 30d
  • loops-in-subagents  ~$320 / 30d
    Long loops: when a task will take more than ~20 tool calls (...), run it in a subagent that reports back a few lines
    evidence: 116 loops of 20+ calls re-read 4,754 calls of history for $353; in subagents that would have been $320 less
  • batch-steps  ~$160 / 30d
    Mechanical steps: combine sequences of `ls`, `cat`, `grep`, `find` and small reads into one command or one script
    evidence: 493 chains of 3+ mechanical calls (2,235 calls); as single scripts they would have saved up to $160
  • scripts-in-files  ~$103 / 30d
  • shell-output      ~$32 / 30d
  • edit-not-write    ~$24 / 30d
```

`--apply` 写入这段（有备份，`--remove` 可删；每次调用约多携带 150 token），并记下当天的行为指标。
一周后 `kvleak refine --probe` 把之后的会话和之前的做对比，每条规则对应一个指标（每次 Bash 结果的 token 数、
机械链里的调用占比、20+ 次调用循环里的调用占比……），告诉你哪条规则的数字动了、哪条没动。这就是 Harness² 的
probe 步骤，只不过跑在你的真实会话上而不是 benchmark 上；它说明行为有没有变，不证明是规则导致的。

**这些会不会损失准确度？** `bench/` 用自动生成、自动判分的任务跑 `claude -p`，分别开关 hooks 和规则。Opus 5.5 上 72 次运行，
每个 arm 每个任务都是满分；在 shell 输出特别长的两个任务上成本降了 16% 到 17%，短任务上没有可测量的差别。
详见 [docs/benchmarks.md §8](benchmarks.md#8-ab-benchmark-accuracy-and-cost-with-and-without-kvleak)。

## 未命中原因分类

| 原因 | 含义 | 修复方向 |
|---|---|---|
| 空闲超过 TTL | 离开超过 5 分钟（或 1 小时），下一条消息全量重建 | 看倒计时及时回复、批量处理、用 `kvleak keepalive` 学出的次数做保活 |
| TTL 内前缀变化 | 时间戳、未排序 JSON、工具列表变化导致前缀击穿 | 前缀确定性：`json.dumps(..., sort_keys=True)`、静态 System Prompt |
| 会话中途切模型 | KV Cache 按模型物理隔离，切换必然全价重算 | 每个 Worker 锁定单一模型，跨模型用文件指针交接 |
| 历史压缩 | `/compact` 或自动压缩重写历史 | 在自然断点主动压缩一次 |
| 冷启动 / 子 Agent 冷启动 | 新 Agent 全价加载系统提示词与工具 Schema | 复用常驻 Worker，保持 System Prompt 完全一致 |

## 多 Agent / 多模型

[多 Agent 缓存手册（英文）](playbook.md) 介绍六层优化：单 Worker 锁定模型并用指针交接、「脚本 → 小模型 → 大模型」算力漏斗、常驻 Worker 与 TTL 窗口批处理、公共看板瘦身契约（15 行以内）、确定性前缀、按水位线主动压缩。手册里附有一段可以直接粘贴进 Swarm 指令的契约。

MIT 协议，欢迎贡献！

## 支持的会话文件

| 来源 | 方式 |
|---|---|
| **Claude Code**（含子 Agent） | 自动：`~/.claude/projects`，尊重 `$CLAUDE_CONFIG_DIR` |
| **Codex CLI** | 自动：`$CODEX_HOME/sessions/**/rollout-*.jsonl` 和 `archived_sessions/`。按 `token_count` 事件取每次响应的用量；OpenAI 把缓存命中和缓存写入都算在 `input_tokens` 里，kvleak 会拆开 |
| **Gemini CLI** | 自动：`~/.gemini/tmp/<project>/chats/session-*.json`（≤0.38）或 `.jsonl`（≥0.39） |
| **OpenCode** | 自动：`$XDG_DATA_HOME/opencode/opencode*.db`（或 `$OPENCODE_DATA_DIR`），1.x 和 2.x 两种表结构；子会话算子 Agent |
| **任何带 usage 字段的 JSONL** | `--path <目录或文件>`，自动识别 Anthropic / OpenAI / Gemini / 扁平 agent 框架字段 |

`audit`、`session`、`advise`、`keepalive`、`whatif` 对以上全部有效；`diet`、`tune`、`hooks`、`recall`、`refine` 需要工具输出本身和 Claude Code 的设置，只读 Claude Code。

## whatif：把别人宣传的数字放到你的账单上

`kvleak whatif` 把每个工具宣传的削减比例作用到账单里它能碰到的那部分：上下文块是"进入成本（一次 cache 写入）+ 之后每次调用携带它的成本"，回复还要加上输出 token。结果是上限，因为它假设模型不会再去取被删掉的内容，而实测恰恰相反：2,848 次计费的 Claude Code 运行里工具输出砍 38%，账单 +6.8%（[arXiv 2607.12161](https://arxiv.org/abs/2607.12161)）；614M token 的真实回放里 rtk 只占账单的 0.5%。`kvleak tune --apply` 那一行就是 `kvleak tune` 打印的总数。输出示例见 [README](../README.zh-CN.md)。

## 到底省没省：`kvleak probe`

`tune --apply`、`hooks --install`、`refine --apply` 会记下执行时间（`~/.cache/kvleak/applied.json`）。
`kvleak probe` 以最早的那个时刻为界（或者 `--at 2026-10-21`、`--at 7d`），把你的交互式会话分成前后两边，对比每次调用的输入花费、整张账单、漏损占比、上下文大小、携带成本、压缩次数、shell 输出 token，并按"之后"这边的调用速率折算成 30 天的差额。只算交互式会话（不含一次性运行和子 Agent），会话按第一次调用落在哪边归属。这是你自己工作上的前后对比，不是对照实验；按次和按比例的行会抵消掉大部分工作内容本身的变化。`--before 14d` 缩短"之前"窗口，`--json` 给脚本用。输出示例见英文指南。
