# kvleak · 从源头降低 Claude Code 的 token 花费

**你的 Claude Code 缓存命中率 95% 以上，输入花费却仍有四分之一用在了缓存重建上。kvleak 告诉你漏在哪，并用一条命令堵上大部分。**  ·  [English](README.md)

[![CI](https://github.com/shangliy/kvleak/actions/workflows/ci.yml/badge.svg)](https://github.com/shangliy/kvleak/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/kvleak)](https://pypi.org/project/kvleak/)
![Python](https://img.shields.io/badge/python-3.9%2B-blue)
![Dependencies](https://img.shields.io/badge/dependencies-0-brightgreen)
![License](https://img.shields.io/badge/license-MIT-green)

![kvleak 审计、whatif 和 tune 在维护者历史上的输出](docs/assets/demo.gif)

<sub>▶ [两分钟视频](https://github.com/shangliy/kvleak/releases/download/v0.9.1/kvleak-demo.mp4)：命中率为什么骗人、漏在哪、怎么修、前后对比、benchmark。</sub>

```bash
uvx kvleak            # 或：pipx run kvleak  ·  pip install kvleak
```

也可以装成 Claude Code 技能，你问"为什么账单这么高"时它自己跑审计：
`npx skills add shangliy/kvleak`，或 `/plugin marketplace add shangliy/kvleak` 后 `/plugin install kvleak@kvleak`。

不需要 API Key、不用配置、不联网、零依赖。它直接读 **Claude Code、Codex CLI、Gemini CLI、OpenCode**
已经存在本机的会话文件，几秒钟出结果。订阅用户看到的美元是按目录价折算的，百分比就是你的限额余量。

```text
 kvleak · prompt-cache leak report
 101 sessions + 276 subagents + 22,100 one-shot runs (Claude Code 81, Codex CLI 20) · 58,208 model calls

  Cache hit rate    95.4%  ██████████████████████▉
  Input spend        $2,602   caching already saved you $21,281
  Output spend      $452.39   replies + thinking at the output price · whole bill $3,054: 43.8% cache writes, 38.8% cache reads, 14.8% output
  Leaked to misses  $537.72   20.7% of input spend went to cache rebuilds and cold starts · 17.6% of the whole bill
  Dead writes       $215.71   write premium on tokens never read back (mostly one-shot `claude -p` runs)

 Where the leak comes from
  cause                            misses  uncached     leak  share
  Idle gap outlived the cache TTL     164     41.3M  $371.92  69.2%  ███████████
  New session cold start            1,378     36.3M   $81.67  15.2%  ██▍
  Subagent cold start                 145      4.1M   $29.44   5.5%  ▉
```

<sub>维护者自己最近 30 天的数据。你的会不一样，这正是要你自己跑一遍的原因。`kvleak --card card.png` 把你的做成[一张图](docs/assets/card.png)，贴到 [#1](https://github.com/shangliy/kvleak/issues/1)。</sub>

## 装上

审计什么都不改。下面四条才会改，每条都能撤销，都只对装完之后**新开**的 Claude Code 会话生效：

```bash
pip install kvleak
kvleak tune --apply          # settings.json：压缩窗口、缓存 TTL、空闲压缩、Bash 输出上限   （--reset 撤销）
kvleak hooks --install       # 长 shell 输出不进上下文；压缩丢掉的内容留档                （--uninstall）
kvleak refine --apply        # 带金额的上下文卫生规则写进 ~/.claude/CLAUDE.md             （--remove）
kvleak statusline --install  # 状态栏缓存倒计时，空闲时也在走                              （--uninstall）
```

怎么确认生效：Claude Code 里输入 `/hooks` 能看到四条 kvleak 记录；第一条回复之后状态栏显示
`● cache 59:47 · 86k ctx · rebuild $1.73`；让它跑一条 `seq 1 20000`，回来的是头、尾加一个路径，
不是两万行。一周后 `kvleak probe` 给出数字。

## 为什么命中率会骗人

一次缓存命中按输入价的 0.1× 计费（Fable 5.1 上是 0.025×），一次重建是 1.25× 到 2×。
200k 上下文上的一次未命中相当于 **80 次命中**，所以 2% 的调用可以吃掉一半的全价输入，
命中率却纹丝不动。kvleak 只把每次未命中里**本可避免**的部分算作漏损：缓存里本来就有的
上下文，按写入价减去读取价计。

## 为什么"token 少了 60–90%"账单却没动

压缩工具输出的工具（rtk 这一类）减少的是上下文**携带**的量，而携带是按 cache 读取价计费的，是账单上最便宜的一行。
2,848 次真实计费的 Claude Code 运行里，工具输出砍掉 38%，账单反而 **+6.8%**（[arXiv 2607.12161](https://arxiv.org/abs/2607.12161)）。
`kvleak whatif` 把每个工具宣传的数字放到你自己的账单上回放，给出上限：

```text
$ kvleak whatif

  What if · 58,199 model calls · $3,053 over the last 30d
  Bill = $80.38 uncached input + $1,337 cache writes + $1,183 cache reads + $452.12 output

  if you…                who promises it                                     bill saving  share of bill
  kvleak tune --apply    compaction window, TTLs, idle compaction, Bash cap  −$786.56             25.8%
  Shell output −60%      rtk's low end ("60–90%")                            −$222.00              7.3%
  All tool results −40%  AgentDiet, arXiv 2509.23586                         −$183.98              6.0%
  Thinking −25%          effort high → medium                                −$59.22               1.9%
  Startup context −50%   claude-token-optimizer                              −$43.19               1.4%
  Replies half as long   caveman                                             −$32.74               1.1%
```

压缩改变的是"到底携带多少"，所以设置这一档的杠杆是最好的压缩器的三倍。这份历史里 44% 的账单是 cache **写入**。

## 三条命令

**1. 看漏损。** `kvleak`（就是上面那张表）。`--since 7d` 限定时间，`--html report.html`
生成可分享的报告，`kvleak session <id>` 逐次调用看一个会话。

**2. 改设置。** Claude Code 自己就能替你压缩、选缓存 TTL、压缩空闲会话，但默认值不是为你
调的。`kvleak tune` 把你最近 30 天在每个设置的每个候选值下重放一遍，`--apply` 把赢家写进
`settings.json`（有备份，`--reset` 撤销）：

```text
$ kvleak tune

  setting                                         now           best   saving / 30d  why
  autoCompactWindow                               auto (~967k)  200k        $720.52  98 compactions instead of 32 · −57.7% of input spend
  `claude -p` runs: CLAUDE_CODE_PROMPT_CACHE_TTL  1h            5m          $169.11  42% of their cache writes were never read back
  CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS             200k          100k         $19.72  153 idle gaps ≥54 min
  bashOutputMaxChars                              30,000        8,000        $32.54  Bash results are 29% of what entered the context

  Total if applied: $909 per 30d (−45% of input spend)
  Write these to ~/.claude/settings.json: kvleak tune --apply
```

一周后 `kvleak probe` 给出应用前后的真实变化：你自己交互式会话里每次调用的输入花费、漏损占比、上下文大小、
压缩次数、shell 输出，以及这个差距折算成 30 天是多少。按实际计费算，不是回放。

**3. 实时看缓存。** `kvleak statusline --install` 在 Claude Code 状态栏放一个 TTL 倒计时，
并在"带着这么大的上下文比压缩它更贵"的时候提醒你：

```text
◕ cache 3:41 · 182k ctx · rebuild $0.89
● cache 59:47 · 249k ctx · rebuild $1.99 · ⚠ /compact saves ~$9.42
◔ cache 1:19 · 250k ctx · rebuild $2.00 · ⚑ ≤2 pings pay
```

## 盒子里还有什么

每一项都先在你自己的历史上测量，再给建议。数字来自维护者的 30 天，方法和复现命令见
[docs/benchmarks.md](docs/benchmarks.md)。

| | | |
|---|---|---|
| `kvleak probe` | 到底有没有省？以 `tune --apply` / `hooks --install` / `refine --apply` 的那天为界，对比前后每次调用的输入花费、漏损占比、上下文大小、压缩次数、shell 输出，并折算成 30 天的数 | 你自己的前后对比，不是回放 |
| `kvleak advise` | **现在**该 `/compact` 还是写交接文档换新会话，几次调用后回本 | 回测中输入花费 −58% |
| `kvleak diet` | 上下文是被什么填满的：shell 输出 29%、发给 shell 的脚本 21%、思考 14%、文件读取 4%；点名最贵的单项 | 114 个长工具循环重读历史花了 $351，放进子 Agent −$319 |
| `kvleak hooks --install` | Claude Code hook：长 shell 输出不进上下文（头、尾、报错行、完整路径），压缩丢掉的全部归档，`kvleak recall` 能找回 | 72 次 benchmark 准确度 1.00，长输出任务 −16% |
| `kvleak refine --apply` | 有证据门槛的上下文卫生规则写进 `CLAUDE.md`，每条附金额；之后 `--probe` 看哪条真的改变了行为 | 5 条规则覆盖 ≈$640 / 30 天 |
| `kvleak skills` | 每个技能的描述都在每次调用的缓存前缀里，调用过的技能正文会一直带到下次压缩。算出列表的花费、点名从未调用的技能和各自的关闭方法、每个已加载正文的携带成本和同会话重复注入 | 19 个技能里 12 个从未调用：列表花费 $153 / 30 天 |
| `kvleak keepalive` | 从你自己的停顿学出保活 ping 几次再放弃才划算，整体和分时段；预算交给 [cache-refresher](https://github.com/olddonkey/cache-refresher) | 5 次 ping 值 $337；午饭停顿 25% 回来，早 6–8 点 86% |
| `kvleak pointer`、`--fail-over`、`--path` | 多 Agent 指针交接、按漏损比例做 CI 门禁、任何带 usage 字段的 JSONL（Anthropic / OpenAI / Gemini） | Swarm 见[手册](docs/playbook.md) |

`audit`、`advise`、`keepalive`、`whatif` 会自动读 Claude Code、Codex CLI（`~/.codex/sessions`）、Gemini CLI（`~/.gemini/tmp/*/chats`）和 OpenCode（`opencode.db`）。
上下文层面的命令（`diet`、`tune`、`hooks`、`refine`）需要工具输出本身，只读 Claude Code。

带完整输出的全部功能：**[docs/guide.zh-CN.md](docs/guide.zh-CN.md)**。每个数字怎么算的：
[docs/how-it-works.md](docs/how-it-works.md)。ccusage 告诉你花了多少，kvleak 告诉你为什么以及改哪里；
和 rtk、caveman、claude-token-optimizer、cache-refresher 的关系见 [docs/compare.md](docs/compare.md)。

## FAQ

**我的数据会被发出去吗？** 不会。只读本地文件，不联网。

**我是订阅用户。** 未命中吃掉的限额余量和 API 上花掉的钱是一回事，把数字当相对权重看。

**削上下文会不会让模型变笨？** A/B benchmark（`bench/`，Opus 5.5 上 72 次）每个 arm 每个任务都是满分。
`tune` 写入的设置只在上下文超过 200k 时压缩，`--min-window` 可以调高。

## 致谢

倒计时的点子来自 [olddonkey/cache-refresher](https://github.com/olddonkey/cache-refresher)；
hooks、recall、refine 借鉴了 [Prime Agent](https://github.com/PrimeIntellect-ai/prime-agent) 和
[Harness²](https://github.com/google-research/harness2)。MIT 协议。
