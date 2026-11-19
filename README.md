# GTA VI trophy rarity forecast

[![make.fun](https://make.fun/badge/gta6-trophy-rarity-forecast-01.svg)](https://make.fun/bounty/gta6-trophy-rarity-forecast-01)
[![Rank](https://make.fun/badge/gta6-trophy-rarity-forecast-01/RenaldiRenaldi/gta6-trophy-rarity-forecast-01/rank.svg)](https://make.fun/bounty/gta6-trophy-rarity-forecast-01)
[![Reward](https://make.fun/badge/gta6-trophy-rarity-forecast-01/RenaldiRenaldi/gta6-trophy-rarity-forecast-01/reward.svg)](https://make.fun/bounty/gta6-trophy-rarity-forecast-01)

Deterministic Python forecasts of Exophase trophy earn rates, with five quantiles per trophy. Built for the [make.fun competition](https://make.fun/bounty/gta6-trophy-rarity-forecast-01).

Run `MAKEFUN_INPUT_DIR=input MAKEFUN_OUTPUT_DIR=out python3 main.py` with Python 3.13. All dependencies are in the standard library. Every input file ending in `.in` receives a matching `.out`, processed in filename order. The sandbox defaults are `/input` and `/output`.

The model deduplicates unchanged snapshots and fits robust lines to logit rates against log elapsed launch hours. Full-history and recent-day fits form a predictive mixture. Fit residuals, slope uncertainty, finite cohort noise and forecast distance contribute uncertainty. Optional objectives use a mixture with a saturating participation ceiling; platinum is constrained by its prerequisites.

An initial tiny cohort needs a separate treatment: a documented launch-night refresh expanded the denominator roughly sixfold. Before any refresh has been seen, a dilution prior and an unrefreshed upper-tail component describe that uncertainty. This launch-only prior is disabled when updated observations arrive. Fewer than three observations use the specified persistence fallback.

The kit supplies no answering reference or supported local grade. Official practice pinball loss, typical miss and coverage therefore remain unmeasured until a server grade. Synthetic stress checks are development diagnostics, not official scores or proof of final calibration.
