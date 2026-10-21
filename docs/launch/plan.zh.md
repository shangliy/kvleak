# kvleak 宣传 pipeline、时间线与内容

一句话定位，所有渠道都用它：**命中率 95% 的 Claude Code，五分之一的输入花费仍在重建缓存；"省 60–90% token"的工具不省钱，四个自带设置才省。** 每篇内容只讲这一个反直觉点，其余功能当附录。

## 0. Pipeline 总览

```
素材 ──→ 首发（HN + X）──→ 扩散（Reddit / 中文）──→ 数据回流（issue #1、probe）──→ 第二波（别人的数字）──→ 长尾（列表、插件市场、newsletter）
 D-1        D0                  D0–D2                    D1–D14                       D7–D10                    D10+
```

每一步的产出都是下一步的输入：首发带来 issue #1 的数字，数字变成第二波内容，第二波内容拿去投 newsletter 和列表。

## 1. 素材清单

| 素材 | 位置 | 状态 |
|---|---|---|
| **讲解视频 2:23，1080p 带音频**（命中率骗人 → 看漏损 → 改设置 → 前后对比 → benchmark → 四条命令 → probe） | 原片 `~/Downloads/kvleak-demo.mp4`；公开地址 https://github.com/shangliy/kvleak/releases/download/v0.9.1/kvleak-demo.mp4 | 完成；X 线程第 1 条、Reddit 帖用它 |
| X 版（≤ 2:20，非 Premium 账号的上限） | `~/Downloads/kvleak-demo-x-140s.mp4` | 完成，只剪掉片尾 3.8 s |
| 视频缩略图 | `~/Downloads/kvleak-thumb-leak.png`（$537.72 页）、`~/Downloads/kvleak-thumb-before-after.png` | 完成，YouTube 封面 / Reddit 预览 |
| 终端演示 GIF（audit → whatif → tune，35 s） | `docs/assets/demo.gif` | 完成，README 顶部；GitHub 不会内嵌播放 release 附件，所以 README 保留 GIF、下面加视频链接 |
| 审计报告截图 | `docs/assets/report.png` | 完成 |
| 状态栏倒计时录屏 | `asciinema rec -c claude x.cast` → `agg --speed 2 x.cast x.gif` | **待你录**，推文 7 用 |
| README（英/中） | `README.md` / `README.zh-CN.md` | 完成，"装上"四条命令在审计块下方 |
| 博客长文 | `docs/blog/index.html` | 完成；建议开 GitHub Pages（Settings → Pages → main `/docs`）得到固定链接 |
| 数字征集帖 | https://github.com/shangliy/kvleak/issues/1 | 完成，所有内容结尾都指向它 |
| 发帖文案 | `docs/launch/en.md`（HN、Reddit、X 线程、HN 问答）、`tweets.md`（10 条）、`zh.md`（即刻、V2EX） | 完成，数字与 README 一致 |
| 本周自己的 probe 结果 | `kvleak tune --apply` 一周后 `kvleak probe` | **待你 apply** |

## 2. 时间线（D0 = 2026-10-21 周三）

| 日 | 时间（太平洋） | 动作 | 内容来源 |
|---|---|---|---|
| D-1 周二晚 | 任意 | 干净环境跑 `uvx kvleak` 确认 0.9.1 可用；录状态栏 GIF；生成 mp4；在自己机器上 `tune --apply` 让 probe 开始计时 | 本文 §1 |
| **D0 周三** | **6:30–7:00** | **Show HN**。发完前两小时守评论区，每条评论 10 分钟内回 | `en.md` Show HN + 问答 |
| D0 | 7:15 | X 七条线程，第 1 条附 140 s 视频，发完置顶 | `en.md` X thread |
| D0 | 9:30 | r/ClaudeAI（标题一），视频帖，正文贴 whatif 表 | `en.md` Reddit |
| D0 | 全天 | HN 若进首页：在 issue #1 贴自己的三张表作示范，降低别人贴数字的门槛 | README 审计块 |
| D1 周四 | 9:00 | r/LocalLLaMA（标题二，"token 少了账单没动"角度） | `en.md` Reddit |
| D1 | 任意 | 即刻 + X 中文一段；V2EX 分享创造 | `zh.md` |
| D1 | 任意 | 推文 1 | `tweets.md` |
| D2 周五 | 9:00 | r/programming 或 Lobsters（二选一，Lobsters 需邀请） | `en.md` |
| D2–D14 | 每天 9:00 | 一条独立推文，顺序 2→10；有人贴数字当天转发那条 | `tweets.md` |
| D3 周六 | | 给 awesome-claude-code、awesome-llm-cost 类列表提 PR | §4.5 |
| D5 周一 | | 投 newsletter：TLDR AI、Console.dev、Python Weekly、Changelog News、Latent Space | §4.6 |
| **D7 周三** | 9:00 | **第二波帖 A："我自己 apply 一周后的 probe 数字"**（X + r/ClaudeAI） | §4.7 模板 A |
| D10 周六 | | **第二波帖 B："N 个人贴了数字，分布长这样"**（issue #1 ≥ 8 条时发；不够就等） | §4.7 模板 B |
| D10 | | Claude Code 插件市场提交（statusline + hooks 打包成 plugin） | §4.8 |
| D14 | | 复盘：star、PyPI 下载、issue #1 条数、HN 分数；决定第三波角度 | §6 |
| D21+ | | 博客二：《"Token Reduction Is Not Cost Reduction"的工具侧回应》投 dev.to / HN（普通链接帖，不是 Show HN） | §4.9 |

规则：Show HN 只能发一次；Reddit 各版块隔天发；同一天不在三个以上渠道首发同一内容。

## 3. 每个渠道讲什么

| 渠道 | 角度 | 钩子 |
|---|---|---|
| Hacker News | 计费不对称 + 研究证据 + 自带设置 | "95% 命中率，五分之一的钱仍在重建" |
| X 英文 | 一图一数字 | GIF + "2% 的调用吃掉一半全价输入" |
| r/ClaudeAI | 实操：四条命令、怎么确认生效、一周后 probe | "Claude Code 已经有这些设置，只是默认值不是为你调的" |
| r/LocalLLaMA | 反主流：压缩工具不省钱 | arXiv 2607.12161 的 +6.8% |
| r/programming / Lobsters | 工程味：零依赖、本地、可复现 benchmark | "4 个 arm 72 次运行准确度 1.0" |
| 即刻 / X 中文 | 同 HN，中文 | `zh.md` 第一段 |
| V2EX / 知乎 | 长文 + 原理 + 公式 | K_max 公式和原因拆解表 |
| newsletter | 一句话 + 一张图 + 链接 | §4.6 的三行邮件 |

## 4. 内容

### 4.1 Show HN（`en.md`，摘要）

标题：`Show HN: kvleak – my Claude Code cache hit rate was 95%, a fifth of input spend still leaked`
正文结构：计费不对称 → 我的 30 天四个数字 → 意外发现（压缩工具不省钱，引研究）→ 四个自带设置、tune −45%、benchmark 准确度不变、probe 测真实前后 → 顺带功能一行 → 求数字 issue #1。

### 4.2 X 线程（`en.md`，6 条）

1 钩子 + GIF → 2 不对称 → 3 午饭 → 4 token ≠ 钱 → 5 四个设置 → 6 CTA。

### 4.3 Reddit 标题

- r/ClaudeAI：I audited 30 days of my Claude Code sessions: 95% cache hit rate, 20% of input spend still leaked to cache rebuilds. Four settings Claude Code already has fixed most of it.
- r/LocalLLaMA：“60–90% fewer tokens” did not shrink my Claude Code bill. Here is what did (with a tool to replay it on yours).

### 4.4 HN 评论预案（`en.md` 有全文）

| 评论 | 回答核心 |
|---|---|
| 我是订阅用户，美元没意义 | 同样的未命中吃同样的限额余量，当相对权重看 |
| 200k 就压缩会不会变笨 | 只在 200k 以上压缩；72 次 benchmark 准确度 1.0；`--min-window 300k`；`--reset` |
| 和 rtk 比呢 | 兼容；whatif 会算；我的账单 44% 是写入，压缩器碰不到 |
| 数据去哪了 | 纯本地，5k 行标准库，没有任何网络 import |
| 只是一个人的数据 | 对，所以 issue #1 在收；三秒钟你就能贴自己的 |
| 这不就是改几个设置吗 | 是，难的是知道改成多少：tune 在你自己的历史上回放每个候选值 |
| Anthropic 该把默认值改好 | 同意；默认值对所有人都不最优，tune 的产出就是每个人自己的值 |

### 4.5 awesome 列表 PR 文案

```
- [kvleak](https://github.com/shangliy/kvleak) - Finds where Claude Code leaks prompt-cache money (idle gaps, prefix changes, cold starts) and replays your own history to pick the compaction window, cache TTLs and Bash cap. Zero dependencies, local only.
```

### 4.6 newsletter 邮件（三行）

```
Subject: kvleak – why a 95% prompt-cache hit rate still leaks 20% of Claude Code spend

Hi <name>, I built kvleak, a zero-dependency CLI that audits the session files Claude Code / Codex CLI / Gemini CLI already keep and prices every cache miss. The surprising finding: tool-output compressors ("60–90% fewer tokens") barely move the bill, because carrying is paid at cache-read price; four settings Claude Code already has cut my input spend 45%, and a 72-run benchmark found no accuracy cost.
Repo + 35-second demo: https://github.com/shangliy/kvleak  ·  HN thread: <link>
```

### 4.7 第二波模板

**A · 一周后自己的 probe（D7）**

```
A week ago I ran `kvleak tune --apply` on my own Claude Code. Today `kvleak probe`:

  input spend per call   $X → $Y   (−Z%)
  leaked share of input  A% → B%
  compactions / session  C → D

Measured on what was billed, not replayed. Same tool, same me, different settings.
<截图>  github.com/shangliy/kvleak
```
如果数字不好看，照发，写明原因（会话类型变了 / 周里短会话多），这比只发好消息更可信。

**B · 别人的数字（D10，≥ 8 条）**

```
N people posted their kvleak audits in a week. Hit rates 92–99%. Leaked share of input spend: median M%, range L–H%. Top cause everywhere: idle gaps past the TTL (K of N).
The hit rate told none of them this. <分布图>  Add yours: github.com/shangliy/kvleak/issues/1
```

### 4.8 Claude Code 插件市场

打包 `statusline` + `hooks` 为 plugin（`plugin.json` + `settings.json` 默认 statusLine 与 hooks），描述一句："Live cache-TTL countdown and lossless compaction for Claude Code, by kvleak." 市场的入口流量比 PyPI 搜索大得多，装完就是三样东西里的两样。

### 4.9 博客二（D21+）

标题：Token reduction is not cost reduction: what 30 days of billed Claude Code sessions say, and the four levers that are.
结构：那篇论文的结论 → 为什么（账单四行里压缩只碰最便宜的一行）→ whatif 表 → tune 表 → probe 前后 → 别人贴的数字分布 → 局限（一人数据、list price、订阅）。

## 5. 每日 15 分钟清单（D0–D14）

1. 回 HN / Reddit / X 的新评论，每条都回，技术性问题附命令。
2. issue #1 有新数字：点赞、回一句你看到的最大原因、转发到 X。
3. 发当天的一条推文。
4. `gh repo view --json stargazerCount` 和 `pypistats recent kvleak` 记一行到 §6 表。
5. 有人提 bug：当天修，当天发 patch 版，并在原帖回复"fixed in x.y.z"。

## 6. 指标与目标

| 指标 | 看哪里 | D0 | D7 目标 | D14 目标 |
|---|---|---|---|---|
| GitHub star | `gh repo view --json stargazerCount` | 0 | 150 | 400 |
| PyPI 下载（周） | `pipx run pypistats recent kvleak` | – | 500 | 1,500 |
| issue #1 数字条数 | issue 页面 | 1（自己） | 8 | 20 |
| HN 分数 | 帖子 | – | ≥ 50（进首页） | – |
| 外部提及 | GitHub 搜索 "kvleak"、X 搜索 | 0 | 3 | 10 |

达不到 D7 目标的判断：HN 没进首页 → D10 在 r/programming 换角度再发一次；进了首页但 issue #1 没人贴 → 降低门槛，在 README 加 `kvleak --share` 一键生成脱敏的贴帖文本（可做）。

## 7. 不做的事

- 不买推广、不刷 star、不在别人的 issue 里打广告。
- 不发任何自己没跑过的数字；所有数字能在 `docs/benchmarks.md` 复现。
- 不在同一个渠道 7 天内发两次主帖。
