# Changelog

## 0.5.0 (2026-10-20)

- `kvleak diet` splits the model's own output into **thinking** (exact, from `usage.output_tokens_details`),
  **tool-call inputs** per tool (the scripts sent to Bash, the files Written) and **text**, and reports the **turn
  structure**: tool loops of 20+ calls, what re-reading their starting history cost, and what running them in a
  fresh subagent would have saved. Shows thinking per call by model and effort level from your own history.
- Correction: 0.4.0 attributed 59% of context growth to "assistant output" by weighting replies with their full
  `output_tokens`. With thinking measured exactly, the maintainer's history is 29% shell output, 21% scripts sent to
  the shell, 14% thinking, 10% pasted text, 6% system prompt, 5% Written files, 4% file reads.
- `kvleak tune` adds `bashOutputMaxChars` (backtested cap on shell output) and an `effortLevel` row that prices
  high→medium from your own thinking-per-call numbers (on this history: $3 per 30 days, so not a lever).

## 0.4.0 (2026-10-20)

- `kvleak diet`: what filled the context (tool results by tool, your messages, the assistant's replies, the system
  prompt), calibrated to the API's usage numbers, with a carried cost per block and the costliest single items
  named. `--all` aggregates 30 days. On the maintainer's history, 59% of everything that entered the context was
  the assistant's own replies and in-turn thinking, 21% shell output, and only 4% file reads.

## 0.3.1 (2026-10-20)

- Cold-cache-aware advice: when the TTL has expired, `kvleak advise` and the statusline (`⚠ /compact first saves ~$X`)
  price the compaction at a full-price read and the alternative at a full rebuild, so they recommend compacting
  *before* the next message.
- `kvleak tune` spots parallel subagent launches that all start cold (140 of 271 in the maintainer's history) and
  prices launching one first so the rest read the shared prefix.

## 0.3.0 (2026-10-20)

- `kvleak tune`: replays your history under every value of the cache settings Claude Code exposes
  (`autoCompactWindow`, `promptCacheTtl`, `subagentPromptCacheTtl`, `CLAUDE_CODE_PROMPT_CACHE_TTL` for one-shot
  `claude -p` batches, `CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS`) and shows what each would have saved. `--apply` writes
  the winners to `~/.claude/settings.json` (backed up; `--reset` removes them again). On the maintainer's 30 days:
  **−$909 / 30d (−45% of input spend)**, most of it from an auto-compact window of 200k instead of ~967k.
- Audit reports **dead writes**: tokens written at the cache-write premium and never read back (for one-shot
  `claude -p` runs that is ~42% of their bill).
- The compaction advisor learns the real post-compaction size from your history instead of assuming 33k.
- Statusline uses Claude Code's own `prompt_cache` / `context_window.current_usage` fields (v2.1.251+) and only
  reads the transcript when a compaction hint is possible, so it is faster and exact.
- Backtests no longer double-credit a compaction after a real one.

## 0.2.0 (2026-10-20)

- `kvleak advise`: should you `/compact` or hand off to a fresh session now? Break-even calls, expected saving,
  learned from your own session history (`~/.cache/kvleak/profile.json`). `--backtest` replays history with the
  advisor in the loop.
- Statusline shows `⚠ /compact saves ~$X` when carrying the context costs more than shrinking it.
- Transcript tail parsing for very large sessions.

## 0.1.0 (2026-10-19)

- First release: `audit`, `session`, `breakeven`, `statusline`, `pointer`, `models`.
- Claude Code reader (incl. subagents, compaction markers, 5m/1h TTL breakdown) and generic JSONL reader
  (Anthropic / OpenAI / Gemini / flat agent-framework usage keys).
- Terminal, JSON, Markdown and self-contained HTML reports; `--fail-over` CI gate.
