# Changelog

## 0.10.1 (2026-10-21)

- `kvleak --card card.png`: a shareable card of the audit (headline numbers, top three causes, concentration).
  Hand-written SVG, zero dependencies; rasterised to PNG when rsvg-convert, ImageMagick, Inkscape or Chrome is
  installed.
- The report says how concentrated the leak is: the share carried by the top five sessions and by sessions over
  200 calls, and how many sessions make up half of it (`concentration` in `--json`).
- Installable as a Claude Code skill (`npx skills add shangliy/kvleak`) and plugin
  (`/plugin marketplace add shangliy/kvleak`): `skills/claude-code-token-cost/SKILL.md` runs the audit when
  you ask about token cost and recommends by evidence; `.claude-plugin/` manifests.
- `docs/compare.md`: how kvleak relates to ccusage, rtk, caveman, claude-token-optimizer and cache-refresher.
- PyPI keywords and description name token cost and the other CLIs; sdist no longer ships launch material,
  demo assets or the benchmark.

## 0.10.0 (2026-10-21)

- `kvleak skills`: what the skill listing costs on every call (every SKILL.md's description is in the cached
  prefix of every session: user, synced, enabled-plugin and bundled skills), which skills were never invoked in
  the window and how to turn each off, and what each loaded skill body cost to carry until the next compaction,
  including repeat injections of a skill already in the context. `--json`.
- `kvleak diet` classifies injected skill bodies as their own kind; `kvleak whatif` gains an "Unused skills off"
  row; `kvleak refine` proposes a "skills-once" rule when repeat injections cost enough.

## 0.9.1 (2026-10-21)

- `kvleak statusline --install` now also sets `refreshInterval` (default 5 s, `--refresh N`, `0` for events
  only). Claude Code re-runs a statusline on events alone, so the countdown used to freeze while you read.
- README: a "Turn it on" block with the four commands that change something, how to check each took effect.

## 0.9.0 (2026-10-21)

- `kvleak probe`: did it help? `tune --apply`, `hooks --install` and `refine --apply` now record when they ran;
  `probe` splits your interactive sessions at that moment (or `--at DATE`) and contrasts input spend per call,
  whole bill per call, leak share, context per call, carried cost, compactions per session and Bash result
  tokens before vs after, with a 30-day projection at the after-side call rate. Measured on what was billed,
  not replayed. `--json` for scripts.

## 0.8.0 (2026-10-21)

- Reads **Codex CLI** (`~/.codex/sessions`), **Gemini CLI** (`~/.gemini/tmp/*/chats`) and **OpenCode**
  (`opencode.db`, 1.x and 2.x schemas) session stores automatically, next to Claude Code. `audit`, `session`,
  `advise`, `keepalive` and `whatif` work across all of them; the audit header says which tools the sessions came
  from. OpenAI prices (GPT-5, GPT-5.6 Terra/Luna/Sol, GPT-6 Astra) are built in.
- `kvleak whatif`: replays what other tools advertise (rtk's shell-output cut, AgentDiet's tool-result cut,
  startup trimming, shorter replies, less thinking) on your own bill as upper bounds, next to `kvleak tune`'s
  total. On the maintainer's 30 days: tune $787 (25.8% of the bill), the best compressor $222 (7.3%).
- The audit shows the **whole bill**: output spend, and the split between uncached input, cache writes, cache
  reads and output (44% / 39% / 15% here). Leak is also given as a share of the whole bill.

## 0.7.0 (2026-10-20)

- `kvleak keepalive`: a keep-alive budget learned from your own pauses. For every pause that outlived the TTL
  (including the ones that ended the session) it prices "ping up to k times, then give up" against never
  pinging, overall and per 3-hour local bucket of when the pause began, and recommends the knee of the curve.
  On the maintainer's 30 days: 323 pauses, 164 never came back, 5 pings worth $337 (optimum 14, $413); lunch
  pauses returned 25% of the time, 6–8 am ones 86%. kvleak does not send the ping; the budget is for a refresher
  such as cache-refresher's `/cache-auto on <k>`.
- Statusline shows `⚑ ≤k pings pay` in the last two minutes of the countdown, using the budget for the current
  hour. `kvleak audit` stores the budget in `profile.json` alongside the session-length profile.
- `bench/`: a before/after benchmark. Six generated, self-grading tasks (verbose test suites, a 130k-line incident
  log, a 17k-line build log, cross-file renames, data bugs, a deprecation walk) run through `claude -p` in four
  arms: baseline, hooks, rules, both. First result (Opus 5.5, 3 seeds, 72 runs): accuracy 1.00 in every arm, cost
  −16% to −17% on the two long-output tasks, within noise elsewhere. `python bench/run.py --selftest` checks the
  graders without a model.
- `refine.RULE_TEXT` holds the rule wording so the benchmark and `propose()` share it.

## 0.6.0 (2026-10-20)

Three things borrowed from REPL-style harnesses (Prime Agent's "context as a variable" and retrievable history,
Harness²'s propose/probe/compose loop), done with what Claude Code exposes: hooks and `CLAUDE.md`.

- `kvleak hooks --install`: four Claude Code hooks. `PostToolUse` on Bash archives any result over 8k chars and
  shows the model head + tail + error lines (with the lines after them) + repeated lines collapsed + the path to
  the full text. `PreCompact` archives every block the compaction is about to drop; `PostCompact` saves the
  summary; `SessionStart` after a compaction tells the model the archive exists, maps its requests and gives it the
  `kvleak recall` command. Hooks never fail loudly (exceptions go to `~/.cache/kvleak/hooks.log`).
- `kvleak recall "<regex>" [--session ID] [--show N]`: search the archived history; the model runs it too.
- `kvleak diet` counts **mechanical chains**: 3+ consecutive ls/cat/grep/read steps with no real decision between
  them, priced as the context re-reads one script would have skipped. On the maintainer's history: 493 chains,
  2,235 of 9,924 calls, up to $160.
- `kvleak refine`: evidence-gated context-hygiene rules for `CLAUDE.md` (loops in subagents, batch mechanical steps,
  scripts in files, shell output to files, Edit not Write, terse in loops), each with its dollar evidence written
  in. `--apply` writes a removable block and stores a behaviour baseline; `--probe` contrasts the sessions since
  with it, one metric per rule.
- `kvleak hooks`/`statusline` share one resolver for how settings.json should invoke kvleak; `--project` installs
  hooks into `./.claude/settings.json`.

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
