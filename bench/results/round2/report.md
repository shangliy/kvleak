# kvleak benchmark: round2

72 runs · tasks: build-error, data-aggregate, deprecate-walk, log-hunt, noisy-tests, rename-api · arms: baseline, hooks, rules, kvleak

Model: claude-opus-5-5 · cost = Claude Code's `total_cost_usd` estimate (list prices; on a subscription this is quota, not dollars) · input $ = kvleak list-price pricing of the transcript

## Per arm (means over all runs)

| arm | runs | accuracy | cost (CC est.) | input $ (kvleak) | calls | context carried (tok, sum of prompts) | Bash result tokens | chains | wall s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline | 18 | 1.00 | $0.21 | $0.17 | 5.0 | 135,315 | 5,464 | 1.0 | 25 |
| hooks | 18 | 1.00 (+0%) | $0.21 (+3%) | $0.18 (+2%) | 5.3 (+7%) | 144,963 (+7%) | 5,642 (+3%) | 1.0 (+0%) | 25 (-0%) |
| rules | 18 | 1.00 (+0%) | $0.20 (-5%) | $0.16 (-5%) | 4.5 (-10%) | 120,229 (-11%) | 3,772 (-31%) | 1.0 (+0%) | 24 (-1%) |
| kvleak | 18 | 1.00 (+0%) | $0.20 (-2%) | $0.17 (-3%) | 4.9 (-2%) | 130,582 (-3%) | 4,003 (-27%) | 0.9 (-6%) | 27 (+7%) |

## Per task

| task | arm | runs | accuracy | cost | calls | context carried | Bash result tokens | notes |
|---|---|---:|---:|---:|---:|---:|---:|---|
| build-error | baseline | 3 | 1.00 | $0.21 | 5 | 153,873 | 8,731 | behaviour2_ok=True; behaviour_ok=True; build_ok=True |
| build-error | hooks | 3 | 1.00 | $0.22 | 6 | 163,818 | 7,103 | behaviour2_ok=True; behaviour_ok=True; build_ok=True |
| build-error | rules | 3 | 1.00 | $0.17 | 5 | 121,573 | 2,710 | behaviour2_ok=True; behaviour_ok=True; build_ok=True |
| build-error | kvleak | 3 | 1.00 | $0.18 | 5 | 129,789 | 3,541 | behaviour2_ok=True; behaviour_ok=True; build_ok=True |
| data-aggregate | baseline | 3 | 1.00 | $0.19 | 4 | 109,360 | 2,750 | types_correct=5/5 |
| data-aggregate | hooks | 3 | 1.00 | $0.20 | 5 | 119,345 | 2,144 | types_correct=5/5 |
| data-aggregate | rules | 3 | 1.00 | $0.19 | 4 | 104,023 | 2,237 | types_correct=5/5 |
| data-aggregate | kvleak | 3 | 1.00 | $0.20 | 5 | 131,072 | 3,170 | types_correct=5/5 |
| deprecate-walk | baseline | 3 | 1.00 | $0.21 | 5 | 134,943 | 4,795 | changelog_ok=True; hidden_tests=61/61 |
| deprecate-walk | hooks | 3 | 1.00 | $0.22 | 5 | 135,557 | 6,681 | changelog_ok=True; hidden_tests=61/61 |
| deprecate-walk | rules | 3 | 1.00 | $0.20 | 4 | 115,543 | 5,192 | changelog_ok=True; hidden_tests=61/61 |
| deprecate-walk | kvleak | 3 | 1.00 | $0.21 | 5 | 144,492 | 3,806 | changelog_ok=True; hidden_tests=61/61 |
| log-hunt | baseline | 3 | 1.00 | $0.25 | 7 | 185,491 | 6,593 | count_ok=True; hidden_tests=5/5; key_ok=True; request_id_ok=True |
| log-hunt | hooks | 3 | 1.00 | $0.24 | 6 | 175,259 | 6,549 | count_ok=True; hidden_tests=5/5; key_ok=True; request_id_ok=True |
| log-hunt | rules | 3 | 1.00 | $0.23 | 6 | 162,563 | 3,130 | count_ok=True; hidden_tests=5/5; key_ok=True; request_id_ok=True |
| log-hunt | kvleak | 3 | 1.00 | $0.21 | 5 | 132,220 | 2,734 | count_ok=True; hidden_tests=5/5; key_ok=True; request_id_ok=True |
| noisy-tests | baseline | 3 | 1.00 | $0.20 | 5 | 123,635 | 5,715 | bugs=6; fixed=6; remaining_failures=0; suite_ok=True |
| noisy-tests | hooks | 3 | 1.00 | $0.20 | 5 | 134,362 | 6,292 | bugs=6; fixed=6; remaining_failures=0; suite_ok=True |
| noisy-tests | rules | 3 | 1.00 | $0.20 | 4 | 109,998 | 5,760 | bugs=6; fixed=6; remaining_failures=0; suite_ok=True |
| noisy-tests | kvleak | 3 | 1.00 | $0.22 | 5 | 129,554 | 6,530 | bugs=6; fixed=6; remaining_failures=0; suite_ok=True |
| rename-api | baseline | 3 | 1.00 | $0.19 | 4 | 104,588 | 4,203 | files_still_mentioning_fetch_user=0; hidden_tests=41/41 |
| rename-api | hooks | 3 | 1.00 | $0.21 | 5 | 141,439 | 5,084 | files_still_mentioning_fetch_user=0; hidden_tests=41/41 |
| rename-api | rules | 3 | 1.00 | $0.19 | 4 | 107,676 | 3,600 | files_still_mentioning_fetch_user=0; hidden_tests=41/41 |
| rename-api | kvleak | 3 | 1.00 | $0.20 | 4 | 116,369 | 4,236 | files_still_mentioning_fetch_user=0; hidden_tests=41/41 |

## Paired: baseline vs kvleak on the same task and seed

| task | seed | accuracy | cost | calls |
|---|---:|---|---|---|
| build-error | 1 | 1.00 → 1.00 | $0.21 → $0.18 (-14%) | 5 → 5 |
| build-error | 2 | 1.00 → 1.00 | $0.21 → $0.18 (-16%) | 5 → 5 |
| build-error | 3 | 1.00 → 1.00 | $0.22 → $0.18 (-18%) | 6 → 5 |
| data-aggregate | 1 | 1.00 → 1.00 | $0.18 → $0.20 (+9%) | 4 → 5 |
| data-aggregate | 2 | 1.00 → 1.00 | $0.18 → $0.21 (+16%) | 4 → 5 |
| data-aggregate | 3 | 1.00 → 1.00 | $0.20 → $0.20 (+1%) | 5 → 5 |
| deprecate-walk | 1 | 1.00 → 1.00 | $0.21 → $0.22 (+9%) | 5 → 6 |
| deprecate-walk | 2 | 1.00 → 1.00 | $0.20 → $0.21 (+3%) | 4 → 5 |
| deprecate-walk | 3 | 1.00 → 1.00 | $0.22 → $0.21 (-7%) | 6 → 5 |
| log-hunt | 1 | 1.00 → 1.00 | $0.25 → $0.21 (-15%) | 6 → 5 |
| log-hunt | 2 | 1.00 → 1.00 | $0.24 → $0.20 (-20%) | 7 → 4 |
| log-hunt | 3 | 1.00 → 1.00 | $0.25 → $0.21 (-17%) | 7 → 6 |
| noisy-tests | 1 | 1.00 → 1.00 | $0.20 → $0.19 (-2%) | 5 → 4 |
| noisy-tests | 2 | 1.00 → 1.00 | $0.20 → $0.23 (+19%) | 5 → 5 |
| noisy-tests | 3 | 1.00 → 1.00 | $0.19 → $0.23 (+18%) | 4 → 5 |
| rename-api | 1 | 1.00 → 1.00 | $0.18 → $0.20 (+11%) | 4 → 5 |
| rename-api | 2 | 1.00 → 1.00 | $0.20 → $0.20 (+1%) | 4 → 4 |
| rename-api | 3 | 1.00 → 1.00 | $0.19 → $0.20 (+4%) | 4 → 4 |

kvleak cheaper in 8 of 18 pairs; mean accuracy 1.00 → 1.00; mean cost $0.21 → $0.20.

Runs that hit the turn cap or budget cap: none
