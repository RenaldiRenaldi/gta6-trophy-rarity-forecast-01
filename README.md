# GTA VI trophy rarity forecast

[![make.fun](https://make.fun/badge/gta6-trophy-rarity-forecast-01.svg)](https://make.fun/bounty/gta6-trophy-rarity-forecast-01)
[![Rank](https://make.fun/badge/gta6-trophy-rarity-forecast-01/RenaldiRenaldi/gta6-trophy-rarity-forecast-01/rank.svg)](https://make.fun/bounty/gta6-trophy-rarity-forecast-01)
[![Reward](https://make.fun/badge/gta6-trophy-rarity-forecast-01/RenaldiRenaldi/gta6-trophy-rarity-forecast-01/reward.svg)](https://make.fun/bounty/gta6-trophy-rarity-forecast-01)

Deterministic Python forecasts of Exophase trophy earn rates, with five quantiles per trophy. Built for the [make.fun competition](https://make.fun/bounty/gta6-trophy-rarity-forecast-01).

Run `MAKEFUN_INPUT_DIR=input MAKEFUN_OUTPUT_DIR=out python3 main.py` with Python 3.13. All dependencies are in the standard library. Every input file ending in `.in` receives a matching `.out`, processed in filename order. The sandbox defaults are `/input` and `/output`.

Round 2 separates earner counts from tracked-player counts: approximate earners are `printed_rate * players / 100`, with binomial and two-decimal rounding uncertainty. A single log-time player trajectory detects refresh steps at every age, using a four-standard-deviation count threshold; later cohort additions have declining log-time exposure. Continuous saturating earner curves keep observations across refreshes. The documented first refresh was **638 → 3,868 players (×6.06)**. Full-history and recent-day fits compete over unlock delays and three capacity hypotheses per kind, with capacities scaled from the latest cohort. Candidates are compared against the same complete history; a shorter window cannot win merely by dropping difficult observations. Fit-quality-weighted mixture CDFs yield quantiles of predicted earners divided by predicted players, capped at 100%. Platinum stays below every prerequisite at every quantile.

For a stale tiny cohort, the documented dilution prior is retained, but the six-percent unrefreshed tail is removed. Its former mixture median is preserved analytically. Tail quantiles respect the printed rate's two-decimal bins, including exact zero. Measured on the supplied practice outputs, median implied logit **σ falls from 0.694373 to 0.446304**; all 110 practice medians are **exactly unchanged**. This measurement inverts each output as `(logit(q95) - logit(q05)) / (2 * 1.645)`; it is not a component-spread estimate or an official score.

Diffusion scales with the documented log refresh magnitude, amortized over a 30-day exposure assumption, rather than the house's fixed coefficient. One observed shock cannot identify a daily variance: that amortization and the future-refresh law remain modeling assumptions tested synthetically. Separate denominator risk grows with forecast player growth. Saturation uncertainty uses three ceilings (0.55/1/1.8 times the kind capacity, bounded above observed earners), rather than a point ceiling. Sparse reads retain the original persistence fallback, and rounded-zero trophies retain a delayed-wave component.

## Component decisions

### Round 3 launch calibration

The practice-shaped branch applies only to one unique snapshot recorded within six hours of launch, a 90-minute horizon, and a cohort below 1,500 players. Its per-kind logit adjustments are intro +0.16, story +0.22, optional +0.19, completion +0.17, and platinum +0.15. Its spread is `hypot(0.075, DIFFUSION * sqrt(days))`; the launch-intro 100% special case retains its prior spread. A one-snapshot case outside this launch window uses the wide refresh spread, and multi-snapshot cases keep the trend model's horizon-scaled spread.

The supplied kit has no answer key, so it cannot provide an official practice loss. The R2 board result was 0.13 loss and 0.04 percentage points of median miss; the analysis grid found 0.100 loss at a uniform +0.20 logit shift, with about 0.03 residual cost attributed to group/trophy differences. Against that analysis proxy (refresh inversion plus +0.20 logit, with the ceiling-intro exception), the current practice forecasts score 0.021922 loss and have mean absolute median logit error 0.029728, excluding the ceiling intro. The proxy median error falls from 0.058146 for R2 to 0.029728 for R3, but the exact per-kind calibration remains unverified without the answer reads. These are proxy measurements, not official results.

Hierarchical slope shrinkage remains available through the observed-history rolling holdout gate, but is **disabled for this release** (`SHRINKAGE = False`): the 60-world no-regression comparison did not justify selecting it. Conditional lower-tail dilution is enabled only on multi-snapshot trend cases whose own detected positive later-refresh log steps total at least `log(1.5)`; the practice cases do not meet that condition. The trend spread law, ceiling mixture, intro-100% behavior, platinum constraint, and stale-read deduplication remain intact.

The ceiling mixture is enabled: the point-ceiling ablation loses in every growth family. All candidates remain callable through `forecast` keyword options and are evaluated by `--ablations`; none requires extra dependencies.

## Validation

The kit has no answer key. No synthetic result establishes superiority to any house forecast on the real final. R1, R2, and R3 are evaluated on the **same generated count-space worlds**, not on the earlier rate-space diagnostics, whose numbers are not directly comparable. The two real practice cases contain only one unique historical snapshot and one shared real refresh; their repeated reads are not independent calibration evidence.

The simulator independently specifies log-time player growth, random-time refreshes centered on the observed first multiplier, smaller later cohort additions, varied true earner ceilings, binomial historical count noise, delayed unlocks, and future count innovations. It computes and rounds `100 * E / P` only after generating counts. Development initially used quieter historical noise; the reported release generator uses the actual binomial variance `E * (1 - E/P)`, with a small floor for zero counts. All random draws have fixed seeds. Trophy outcomes share the player trajectory, so the 3,300 scored trophies in each family are correlated; the sample is **60 simulated worlds**, not 3,300 independent experiments.

### Synthetic count-space comparison

| Family (60 trials each) | R1 loss | R2 loss | R1 miss, pp | R2 miss, pp | R1 coverage | R2 coverage |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Refresh-only | 0.247883 | 0.234296 | 0.057041 | 0.057041 | 0.948788 | 0.901515 |
| Growth-only | 0.841984 | 0.611243 | 10.660475 | 9.353978 | 0.884848 | 0.916667 |
| Refresh + growth | 0.820946 | 0.621531 | 9.846512 | 9.550012 | 0.899697 | 0.921818 |
| Delayed unlock | 1.196151 | 0.626684 | 25.658781 | 10.135185 | 0.856364 | 0.923030 |
| High shock | 1.543660 | 1.365515 | 18.648792 | 18.014531 | 0.749091 | 0.616061 |

R3 reproduced the R2 losses in these five synthetic families: **0.234379, 0.611243, 0.621531, 0.626684, and 1.365515**, respectively. The synthetic 90-, 1,440-, 2,880-, and 6,510-minute trend holdout losses were **0.087569, 0.289197, 0.369834, and 0.607943**, with 90% coverage **0.948485, 0.885455, 0.916061, and 0.913636**. The conditional asymmetry gate is separately tested with ordinary and large observed refresh histories.

Loss is the competition's mean doubled logit pinball loss; miss is mean absolute median error in percentage points; coverage uses raw printed percentages. High-shock worlds have the same historical noise mechanism but larger unseen future innovations. Their low coverage is an explicit stress-test limitation, despite lower loss than R1; this is not a claim of universal 90% calibration.

Synthetic real-refresh calibration (60 worlds, ratio centered on `638/3868`, shared log-ratio SD 0.40, per-trophy log-count SD 0.06): **R1 loss 0.247378 → R2 0.234274**, coverage **0.967273 → 0.911818**, identical median miss **0.059791 pp**. The broader refresh-only family uses SDs 0.42 and 0.10. These are hypothetical variations around one documented refresh, not 60 observed real refreshes.

### Synthetic horizon holdouts

Each horizon has 60 independently seeded worlds, cut at checkpoints 48/52/56/60 hours after launch. Ordinary future log-count innovations have SD `0.11 * sqrt(days)` for both players and earners; the high-shock family doubles this approximately to `0.23 * sqrt(days)`.

| Horizon, minutes | R1 loss | R2 loss | R2 coverage |
| --- | ---: | ---: | ---: |
| 90 | 0.086899 | 0.087569 | 0.948485 |
| 1,440 | 0.359789 | 0.289197 | 0.885455 |
| 2,880 | 0.474410 | 0.369834 | 0.916061 |
| 6,510 | 0.775914 | 0.607943 | 0.913636 |

Coverage remains near 90% without a long-horizon collapse; loss increases with horizon. R2 does not beat R1 on the 90-minute trend holdout, and that small regression is retained in the table rather than hidden.

### Synthetic component ablations (loss)

| Family | Selected R2 / no shrink | Hierarchical candidate | Point ceiling | Explicit asymmetry |
| --- | ---: | ---: | ---: | ---: |
| Refresh-only | 0.234296 | 0.234296 | 0.234296 | 0.234296 |
| Growth-only | 0.611243 | 0.610992 | 0.702467 | 0.611421 |
| Refresh + growth | 0.621531 | 0.620166 | 0.783216 | 0.636072 |
| Delayed unlock | 0.626684 | 0.626677 | 0.763843 | 0.636455 |
| High shock | 1.365515 | 1.368356 | 1.508609 | 1.338283 |

## Reproduce

From this repository, run the required execution, shape, determinism, import, wallet, dense-history, calibration, and horizon checks, then the same worlds with component ablations:

```sh
python3 validate.py --input ../gta6-trophy-rarity-forecast-01-kit/gta6-trophy-rarity-forecast-01-kit/input
python3 validate.py --input ../gta6-trophy-rarity-forecast-01-kit/gta6-trophy-rarity-forecast-01-kit/input --ablations
```

Both commands use 60 trials by default and exit nonzero when a selected-model release check fails. The second reproduces every table above. Family seeds start at 601100/602100/603100/604100/605100, calibration at 817210, and horizon holdouts at 902100; each trial adds its index. R1 and R2 are loaded into memory directly from immutable commits `26dd6075580872a40bac54a1550a89ef9203f15f` and `24415bf`. No baseline files or submissions are changed. `--answers FILE` optionally scores a separately supplied mapping from case ids to trophy-id/actual-rate dictionaries; the kit supplies none.

The execution check runs **`python3 -P -s main.py` twice in a clean environment** with `MAKEFUN_INPUT_DIR` and `MAKEFUN_OUTPUT_DIR` set. Both practice cases produce regular, byte-identical files with 55 valid rows each and 2,906 bytes per file. A 300-unique-snapshot count-space case completes in under one second locally; timings vary by machine. Empty/sparse/null histories, stale-read duplication, log-time slope recovery, horizon regimes, and refreshes after hour 12 are checked. An AST audit examines every tracked Python file: all external imports are standard library modules. The existing wallet claim is verified without alteration.

Tests ran on Python **3.12.3**. Python 3.13 and Docker are unavailable locally; 3.13 compatibility is inspected, not executed. No answer key, official R2 score, final superiority, prize eligibility, or transcript-gate result is claimed. Synthetic performance does not establish superiority on the real final. Bundling, sealing, submission, and repository About settings are left to the operator.
