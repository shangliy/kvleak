# Changelog

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
