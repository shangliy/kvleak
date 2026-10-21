# V2EX（分享创造节点）/ 知乎 正文

**标题：** 开源了一个工具：Claude Code 缓存命中率 95%，为什么还有五分之一的输入花费在缓存重建上？以及"省 60–90% token"为什么不省钱

**正文：**

先说一个反直觉的事实：缓存命中率按 token 算，钱按"未命中次数 × 当时的上下文大小"算。

Anthropic 的 Prompt Cache 计费是不对称的：命中按输入价的 0.1×（Fable 5.1 上 0.025×），重建按 1.25×（1 小时 TTL 是 2×）。200k 上下文漏一次，等于 80 次命中。所以 2% 的调用可以吃掉一半的全价输入，命中率纹丝不动。

我写了个小工具 kvleak，直接读 Claude Code 存在本机 `~/.claude/projects` 里的会话文件，把每次未命中标上原因（空闲超过 TTL、前缀变化、换模型、压缩、新会话冷启动、子 Agent 冷启动），只把本可避免的部分算作漏损。不要 API Key、不联网、零依赖：

    uvx kvleak

我自己最近 30 天（101 个会话，5.8 万次模型调用）：

- 命中率 95.4%，但 20.7% 的输入花费用在了缓存重建上
- 2% 的调用占了一半以上的全价输入
- 69% 的漏损是空闲超时：午饭、开会、过夜。其中 97% 来自离开超过一小时，回来时要重建 150k 到 560k 的上下文
- 很集中：最大的 5 个会话占 51%，14 个超过 200 步的马拉松会话占 56%
- 整个账单 44% 是缓存写入，不是读取也不是输出

**没想到的一点：** 那些宣传"token 少 60–90%"的工具输出压缩器（rtk 这一类）减的是上下文携带量，而携带按缓存读取价计费，是账单上最便宜的一行。一项对 2,848 次真实计费的 Claude Code 运行的研究发现，工具输出砍 38%，账单反而 +6.8%（arXiv 2607.12161）。`kvleak whatif` 把每个工具宣传的数字放到你自己的账单上回放，给出上限：

    if you…                who promises it                                     bill saving  share of bill
    kvleak tune --apply    compaction window, TTLs, idle compaction, Bash cap  −$807.14             26.3%
    Shell output −60%      rtk's low end ("60–90%")                            −$216.35              7.1%
    All tool results −40%  AgentDiet, arXiv 2509.23586                         −$180.47              5.9%
    Unused skills off      kvleak skills: 12 of 19 never invoked               −$161.21              5.3%
    Thinking −25%          effort high → medium                                −$59.34               1.9%
    Startup context −50%   claude-token-optimizer                              −$41.80               1.4%
    Replies half as long   caveman                                             −$33.36               1.1%

**真正有用的是 Claude Code 自带的四个设置**（自动压缩窗口、`claude -p` 的缓存 TTL、空闲压缩、Bash 输出上限），只是默认值不是为任何人调的。`kvleak tune` 用你自己 30 天的记录把每个候选值回放一遍，`--apply` 写进 settings.json（有备份，`--reset` 撤销）。我这里输入花费 −45%，主要来自在 200k 压缩而不是默认的约 967k。72 次 A/B benchmark（Opus 5.5）每个 arm 准确度都是 1.0。一周后 `kvleak probe` 按真实计费对比前后。

装上（每条都能撤销）：

    pip install kvleak
    kvleak tune --apply          # 压缩窗口、缓存 TTL、空闲压缩、Bash 输出上限   （--reset）
    kvleak hooks --install       # 长 shell 输出不进上下文；压缩丢掉的内容留档   （--uninstall）
    kvleak refine --apply        # 带金额的上下文卫生规则写进 ~/.claude/CLAUDE.md（--remove）
    kvleak statusline --install  # 状态栏缓存倒计时                              （--uninstall）

订阅用户看到的美元是按目录价折算的，百分比就是你的限额余量，那才是你真正感觉到的东西。

也支持 Codex CLI、Gemini CLI、OpenCode 的会话文件。MIT 开源：https://github.com/shangliy/kvleak

两分钟视频把原理、数字和四条命令讲完：https://github.com/shangliy/kvleak/releases/download/v0.9.1/kvleak-demo.mp4

很想看别人的数字。审计三秒钟、全本地，可以贴到这个 issue：https://github.com/shangliy/kvleak/issues/1

---

**知乎发法：** 同一篇当"文章"发，标题改成问句"Claude Code 缓存命中率 95%，为什么还有五分之一的钱花在缓存重建上？"，封面用 `docs/assets/demo.gif` 的首帧。另外在"Claude Code 怎么省 token"类问题下回答，用上面"没想到的一点"那段加 whatif 表，末尾放仓库链接。

**即刻发法：** 用 `zh.md` 里的短版，上传 `~/Downloads/kvleak-demo.mp4`，发到"AI 编程"或"Claude"圈子。
