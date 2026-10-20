# 宣传文案（中文）

## 即刻 / 微博 / X 中文

缓存命中率 99.0% 的 Claude Code 会话，照样漏了 82 美元。

Prompt Cache 的计费是不对称的：命中只收 0.1×（Fable 5.1 上只有 0.025×），重建要 1.25–2×。在 Fable 5.1 上，200k 上下文漏一次就是 $3.95，等于 80 次命中。

我把自己 30 天的会话审计了一遍：
- 命中率 97.5%，但 27.5% 的输入花费用在了缓存重建上
- 2% 的调用吃掉了 54% 的全价输入
- 82% 的漏损来自离开电脑：午饭、开会、过夜（Claude Code 现在默认用 1 小时 TTL）

于是做了 kvleak：一行 `uvx kvleak`，读取本地已有的会话记录，给每一次未命中标出原因并折算成美元。另外还有状态栏 TTL 倒计时、保活盈亏计算器，以及一份多 Agent 省 Token 手册（模拟 6 个 Worker 的 Swarm，成本 −86%）。

零依赖、纯本地、MIT 开源 👉 https://github.com/shangliy/kvleak

## V2EX（分享创造）/ 知乎

**标题：** 开源了一个工具：缓存命中率 97.5%，为什么我的 Claude Code 还有 27.5% 的钱花在缓存重建上？

**正文结构：**
1. 反直觉的事实：命中率是按 Token 算的，钱是按「未命中 × 上下文大小」算的。99% 命中率的会话照样漏 82 美元。
2. 计费原理：读 0.1× / 写 1.25×（1 小时 TTL 是 2×），并给出盈亏平衡公式 `K_max = floor((write − read) / read)`。
3. 30 天审计数据：贴 docs/benchmarks.md 里的原因拆解表。
4. 多 Agent 场景的三个放大器：跨模型缓存隔离、公共看板 O(N²) 广播、子 Agent 冷启动税，以及六层优化手册。
5. 使用方法：`uvx kvleak`、`kvleak statusline --install`、`kvleak breakeven 200k --idle 30m`。
6. 欢迎 PR：支持 Codex CLI、Gemini CLI、Aider 等工具的会话记录。
