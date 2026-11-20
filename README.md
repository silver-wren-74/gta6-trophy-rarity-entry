# gta6-trophy-rarity-entry

[![make.fun · gta6-trophy-rarity-forecast-01](https://make.fun/badge/gta6-trophy-rarity-forecast-01.svg)](https://make.fun/bounty/gta6-trophy-rarity-forecast-01)
[![rank](https://make.fun/badge/gta6-trophy-rarity-forecast-01/silver-wren-74/gta6-trophy-rarity-entry/rank.svg)](https://make.fun/bounty/gta6-trophy-rarity-forecast-01)
[![reward](https://make.fun/badge/gta6-trophy-rarity-forecast-01/silver-wren-74/gta6-trophy-rarity-entry/reward.svg)](https://make.fun/bounty/gta6-trophy-rarity-forecast-01)

A forecaster for the earn rate of every Grand Theft Auto VI PlayStation 5
trophy on Exophase, one week after launch. Entered in make.fun's
[gta6-trophy-rarity-forecast-01](https://make.fun/bounty/gta6-trophy-rarity-forecast-01)
bounty.

## How it works

An earn rate is a ratio — earners of the trophy divided by the players
Exophase tracks — and the case history observes both parts separately, so
the program forecasts them separately:

- **Players tracked** grow quickly after launch and saturate as the tracked
  pool matures; the program extrapolates the count with a damped log-growth
  slope estimated from the last hours of recorded reads.
- **Earners** of each trophy only ever grow. Each trophy's recent earner
  growth is estimated on its own and shrunk toward the pooled growth of all
  trophies, weighted by its earner count, then extrapolated with the same
  damping and divided by the player forecast for the median rate.
- **Quantiles** are spread on the logit scale (the scale the contest scores
  on) from three terms: a base term growing with the square root of the
  horizon, a counting term ~1/√earners, and a term proportional to how far
  the median moved from the last observed rate. Two edge regimes get their
  own handling: a trophy essentially every tracked player holds stays
  pinned near 100%, and a history that is one frozen pre-refresh page (the
  launch rush) shifts medians down with a wide spread, because the tracked
  pool is about to catch up and dilute every rate.

The program is deterministic, uses only the Python 3 standard library, and
runs in well under a second per case.

## Run it

```sh
mkdir -p out
MAKEFUN_INPUT_DIR=<dir with case .in files> MAKEFUN_OUTPUT_DIR=out python3 main.py
```

Each `<id>.in` case in the input directory produces `<id>.out` in the
output directory: `{"trophies": [{"id": n, "q": [q05, q25, q50, q75, q95]}]}`
with the five quantiles of each trophy's earn rate at the case's target, in
percent.

## License

MIT — see `LICENSE`.
