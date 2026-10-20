# Before/after benchmark

Does kvleak's `hooks` + `refine` setup make Claude Code cheaper without making
it worse? This directory answers that with a controlled A/B on generated,
self-grading tasks.

```bash
python bench/run.py --selftest                      # graders: reference fix scores 1.0, broken repo scores < 1.0
python bench/run.py --arms baseline,kvleak          # 6 tasks × 2 arms (needs `claude` logged in)
python bench/run.py --arms baseline,hooks,rules,kvleak --repeats 3 --concurrency 3
python bench/run.py --report bench/results/<dir>    # re-render report.md from the saved runs
```

## Arms

| arm | hooks (`--settings`) | `CLAUDE.md` rules |
|---|:-:|:-:|
| `baseline` | | |
| `hooks` | ✓ | |
| `rules` | | ✓ |
| `kvleak` | ✓ | ✓ |

The hooks are exactly what `kvleak hooks --install` writes (`PostToolUse` on
Bash condenses results over 8k chars; `PreCompact`/`PostCompact`/`SessionStart`
archive and recall). The rules are the five `kvleak refine` proposed on the
maintainer's history, evidence lines included, because the model reads them.
Everything else is identical between arms: same `claude -p` prompt, model,
effort, `--max-turns`, `--max-budget-usd`, `--permission-mode bypassPermissions`,
a fresh git repo per run. `kvleak tune`'s settings (auto-compact window, TTLs)
are not part of any arm: the tasks are far too short to reach them.

## Tasks

Each task generates a stdlib-only Python repo from a seed, gives the agent a
prompt, and grades from outside the repo (hidden tests and reference answers
live in `hidden/`, which the agent never sees). `--repeats N` uses N seeds.

| task | what the agent must do | stresses | grade |
|---|---|---|---|
| `noisy-tests` | 10 modules, ~240 verbose unittest tests with a chatty logger; 3 injected bugs | shell output, fix loops | share of the 3 bugs fixed (halved if the suite still fails) |
| `log-hunt` | 42k-line incident log; name the first failing request and the config key, fix the duration parser | shell output, grep chains | 0.4 request id + 0.2 key + 0.4 hidden parser tests |
| `rename-api` | rename `fetch_user` to `load_user(user_id, *, strict=False)` across 18 callers that import it four different ways, tests and docs | chains, edits | hidden tests × (1, or 0.5 if `fetch_user` survives anywhere) |
| `build-error` | `build.py` prints ~9,000 lines; one of 60 modules has a syntax error | shell output | 0.5 build passes + 0.5 the fixed module still computes the right values |
| `data-aggregate` | 25k-row CSV with timezone offsets and duplicate rows; two bugs in the aggregator | shell output, data | share of event types whose numbers match the reference |
| `deprecate-walk` | migrate real `legacy_hash` call sites (3 import styles) among decoys that only mention it in comments, remove it, add a changelog line | chains, edits | 0.9 hidden tests + 0.1 changelog |

## Metrics

Per run: the task score (0..1), Claude Code's own `total_cost_usd` estimate
and `num_turns`, wall time, and from the session transcript via kvleak:
billed calls, sum of prompt sizes (the context that was carried across the
run), peak context, cache reads/writes, output tokens, Bash result tokens,
mechanical chains, and kvleak's list-price input cost. On a subscription the
dollar numbers are quota, not a bill; the relative comparison is the point.

`report.md` gives means per arm with deltas against the first arm, a per-task
table, and paired rows (same task, same seed) for the first and last arm.

## Results so far

`results/round2` (2026-10-20, Claude Opus 5.5, 3 seeds, 4 arms, 72 runs,
$14.78 at list price):

| arm | accuracy | cost / run | calls | Bash result tokens | cheaper than baseline |
|---|---:|---:|---:|---:|---:|
| baseline | 1.00 | $0.21 | 5.0 | 5,464 | – |
| hooks | 1.00 | $0.21 (+3%) | 5.3 | 5,642 | 6 / 18 |
| rules | 1.00 | $0.20 (−5%) | 4.5 | 3,772 | 10 / 18 |
| kvleak | 1.00 | $0.20 (−2%) | 4.9 | 4,003 | 8 / 18 |

Every run in every arm scored 1.0, so nothing here cost accuracy. The cost
differences are within noise: the tasks take 4–7 calls, Opus 5.5 already
tails long output by itself, and the Bash hook fired in only 3 of 18 runs
per hooks arm (the two tasks with 17k- and 130k-line output are the ones
where `kvleak` came out 16–17% cheaper). The settings with the large measured
savings on real history (`kvleak tune`: compaction window, idle compaction,
TTLs) need sessions of hundreds of calls, which these tasks never reach.
Full table and discussion: [docs/benchmarks.md §8](../docs/benchmarks.md#8-ab-benchmark-accuracy-and-cost-with-and-without-kvleak).

## Caveats

* Synthetic tasks, written to exercise what the hooks and rules act on. They
  say nothing about tasks where long shell output is rare.
* One seed per task is a pilot. Use `--repeats 3` or more before quoting a
  number; agent runs vary a lot.
* `bypassPermissions` in a throwaway directory. Do not point the runner at a
  repo you care about.
* The `rules` arm carries ~150 extra tokens per call for the `CLAUDE.md`
  block; that cost is included in its numbers.
