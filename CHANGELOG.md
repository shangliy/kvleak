# Changelog

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
