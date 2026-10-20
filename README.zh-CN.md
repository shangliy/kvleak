# kvleak

**找出你的 AI 编程 Agent 在 Prompt Cache 上漏掉了多少钱，以及怎么堵上。**  ·  [English](README.md)

表面缓存命中率 90%+，账单却依然很贵？因为缓存**未命中比命中贵约 12 倍**，而且未命中虽然少、单次却很大：空闲超过 5 分钟 TTL、会话中途切模型、一次性子 Agent 冷启动……真实审计中，**约 5% 的调用吃掉了约一半的全价输入 Token**。

> **缓存命中率 99.0% 的会话，照样漏了 82 美元。** 在 Claude Fable 5.1 上，一次缓存未命中的成本相当于 80 次命中。一份 30 天的真实 Claude Code 使用审计显示：2% 的调用占了一半以上的全价输入。[查看数据 →](docs/benchmarks.md)

`kvleak` 直接读取本机已有的会话记录，找出每一次昂贵的缓存击穿，告诉你**为什么**发生，并折算成美元。

```bash
uvx kvleak          # 或：pipx run kvleak  ·  pip install kvleak
```

零依赖、零配置、不需要 API Key、不联网。默认读取 `~/.claude/projects`，其他带 token usage 的 JSONL 用 `--path` 指定。

## 真实数据

- 30 天交互式 Claude Code 会话：命中率 **97.5%**，但 **27.5%** 的输入花费用在了缓存重建上
- 其中 **82%** 的漏损来自空闲间隔（中位数 2.8 小时；Claude Code 现在默认使用 1 小时 TTL）
- 200k 上下文一次未命中的成本：Fable 5.1 上 **$3.95**，Opus 5.5 上 $1.56
- 6 个 Worker 的 Swarm 模拟：指针交接 + 常驻 Worker + 锁定模型，输入成本 **−86%**
- 压缩顾问回测：只在上下文超过 200k 时压缩（平均每个会话约 3 次），输入花费 **−58%**；只在超过 400k 时压缩（30 天共 23 次）也有 **−40%**
- `kvleak tune --apply`：同样的 30 天，四个 Claude Code 自带的设置合计 **−$909（−45%）**

## 功能

| 命令 | 作用 |
|---|---|
| `kvleak` | 全量审计：命中率、漏损金额、按原因拆解、最漏的会话、最贵的单次未命中、可执行建议 |
| `kvleak --since 7d --html report.html` | 生成可分享的自包含 HTML 报告（亦支持 `--markdown` / `--json`） |
| `kvleak session <id>` | 单个会话逐步时间线，标出每一次未命中及原因 |
| `kvleak statusline --install` | 在 Claude Code 状态栏显示**缓存 TTL 倒计时**：`◕ cache 3:41 · 182k ctx · rebuild $0.89` |
| `kvleak advise` | 告诉你**现在该不该 `/compact`，或者写一份交接文档换新会话**，以及几次调用后回本；`--backtest` 用你的历史数据回测 |
| `kvleak tune --apply` | 用你的历史回测 Claude Code 自带的缓存设置（自动压缩窗口、缓存 TTL、空闲压缩阈值），把最省钱的值**直接写进 `settings.json`** |
| `kvleak breakeven 150k --idle 20m` | 保活还是放弃？按盈亏平衡点 `K_max = floor((write − read) / read)` 计算 |
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

  Total if applied: $909.35 per 30d
  Write these to ~/.claude/settings.json: kvleak tune --apply  (undo: kvleak tune --reset)
```

`--apply` 会先备份 `settings.json`，`--reset` 只删掉它写入的那几个键。想保留更多细节可以用
`--min-window 300k` 限制自动压缩的下限。审计报告里还新增了**死写入**一项：按 1 小时 TTL
的 2 倍写入价缓存、却从来没被读回的 Token。对一次性的 `claude -p` 批处理来说这占了账单的 42%，
所以那类任务该用 5 分钟 TTL。

## 未命中原因分类

| 原因 | 含义 | 修复方向 |
|---|---|---|
| 空闲超过 TTL | 离开超过 5 分钟（或 1 小时），下一条消息全量重建 | 看倒计时及时回复、批量处理、按盈亏平衡点限制保活次数 |
| TTL 内前缀变化 | 时间戳、未排序 JSON、工具列表变化导致前缀击穿 | 前缀确定性：`json.dumps(..., sort_keys=True)`、静态 System Prompt |
| 会话中途切模型 | KV Cache 按模型物理隔离，切换必然全价重算 | 每个 Worker 锁定单一模型，跨模型用文件指针交接 |
| 历史压缩 | `/compact` 或自动压缩重写历史 | 在自然断点主动压缩一次 |
| 冷启动 / 子 Agent 冷启动 | 新 Agent 全价加载系统提示词与工具 Schema | 复用常驻 Worker，保持 System Prompt 完全一致 |

## 多 Agent / 多模型

[多 Agent 缓存手册（英文）](docs/playbook.md) 介绍六层优化：单 Worker 锁定模型并用指针交接、「脚本 → 小模型 → 大模型」算力漏斗、常驻 Worker 与 TTL 窗口批处理、公共看板瘦身契约（15 行以内）、确定性前缀、按水位线主动压缩。手册里附有一段可以直接粘贴进 Swarm 指令的契约。

MIT 协议，欢迎贡献！
