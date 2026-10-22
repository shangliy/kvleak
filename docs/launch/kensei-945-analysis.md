# kvleak analysis of @0xKense1's public 945-run Claude Code dataset

Date: 2026-10-21
Dataset: https://github.com/kenseii1/claude-code-limits-tests (`runs/**/*.json`, 936 valid JSON logs with token usage)
TL;DR: Re-analyzing @0xKense1's public 945-run dataset using the `costUSD` and cache read/write breakdown recorded inside his own JSON logs flips two headline conclusions from raw-token counting: `/compact` is +14% to +37% more expensive after 3 follow-up questions (breaking even only after ~7–8 questions at N=15/30), and subagent delegation is +29.7% more expensive despite using 3.4% fewer raw tokens.

## 1. `/compact` vs `/clear` vs `continue` (`runs/session/*.json`)

In the original article, options are compared by raw tokens (`input + output + cacheRead + cacheCreation`) across 3 follow-up questions after forking at `N = 5, 15, 30` turns. Because cache reads cost `0.1x` while `/compact` pays `5x` output for the summary, `2x` (1h TTL) to write the new prefix, and drops previously read files from context (forcing follow-up turns to re-open and re-cache files), the `costUSD` recorded by Claude Code in the same JSON files tells a different story:

| History (`N` turns) | Mean context before fork | Arm | 3-question raw tokens (vs `continue`) | 3-question `costUSD` (vs `continue`) | Mean `costUSD` per follow-up question | `/compact` break-even |
|---:|---:|---|---:|---:|---:|---:|
| 5 | 50,751 | `continue` | 539,809 (—) | **$0.1409** (—) | $0.0470 | — |
| 5 | 50,751 | `clear` | 284,308 (−47.3%) | **$0.1460 (+3.6%)** | $0.0487 | — |
| 5 | 50,751 | `compact` | 327,122 (−39.4%) | **$0.1923 (+36.5%)** | $0.0523 (+ $0.0354 `/compact` call) | **never** (at 51k ctx) |
| 15 | 106,892 | `continue` | 1,119,496 (—) | **$0.2228** (—) | $0.0743 | — |
| 15 | 106,892 | `clear` | 270,435 (−75.8%) | **$0.1117 (−49.9%)** | $0.0372 | — |
| 15 | 106,892 | `compact` | 474,870 (−57.6%) | **$0.2544 (+14.2%)** | $0.0660 (+ $0.0565 `/compact` call) | **~6.8 questions** |
| 30 | 155,496 | `continue` | 1,366,859 (—) | **$0.2390** (—) | $0.0797 | — |
| 30 | 155,496 | `clear` | 285,214 (−79.1%) | **$0.1415 (−40.8%)** | $0.0472 | — |
| 30 | 155,496 | `compact` | 549,823 (−59.8%) | **$0.2832 (+18.5%)** | $0.0708 (+ $0.0708 `/compact` call) | **~8.0 questions** |

## 2. Subagents: `-3.4%` in raw tokens, `+29.7%` in `costUSD` (`runs/subagent/*.json`)

| Experiment | Arm | Mean total tokens (4 calls) | Mean total `costUSD` | Main task call `costUSD` | 3 follow-ups `costUSD` |
|---|---|---:|---:|---:|---:|
| `subagent` | `main` | 255,035 | **$0.1169** | $0.0522 | $0.0647 |
| `subagent` | `sub` | 246,263 (−3.4%) | **$0.1516 (+29.7%)** | $0.0930 | $0.0586 |
| `subagent-deep` | `main` | 303,785 | **$0.1604** | $0.1267 | $0.0337 |
| `subagent-deep` | `sub` | 321,681 (+5.9%) | **$0.1883 (+17.4%)** | $0.1552 | $0.0331 |

Why: the subagent starts with a cold cache and writes its own prefix (`$0.0930` vs `$0.0522`), while the lighter main context saves only `$0.0061` across 3 follow-up questions (`~$0.0020` per question). Paying back one subagent cold start takes ~20 follow-up questions in the main chat.

## 3. What `kvleak --path runs` catches on the raw logs

Running `kvleak --path /tmp/claude-code-limits-tests/runs` and `kvleak session 600996ac --path /tmp/claude-code-limits-tests/runs` surfaces two additional cache effects:

1. **8h36m rate-limit pause inside the 30-turn chains (`600996ac` and `35715622`)**:
   - Between turn 28 (`08:07:07 UTC`) and turn 29 (`16:43:43 UTC`), an `8h36m` pause expired the 1-hour cache.
   - Turn 29 rebuilt **142k uncached tokens (`$0.629` vs `$0.092` on turn 28, ~6.8x)** while the 30-turn session still reported a **95.9% token cache hit rate**.
2. **Dead 1-hour cache writes on one-shot `claude -p` runs**:
   - Across the 936 runs (`$79.14` total bill: 58.6% cache writes, 25.3% cache reads, 16.1% output), **$14.13 (~18% of the entire benchmark bill)** went to the 1-hour TTL write premium (`2.0x` vs `1.25x`) on one-shot runs that exited immediately and were never read back. Setting `CLAUDE_CODE_PROMPT_CACHE_TTL=5m` for batch harnesses avoids that premium.
