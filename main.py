#!/usr/bin/env python3
"""GTA VI trophy earn-rate forecaster (make.fun: gta6-trophy-rarity-forecast-01).

Reads one case file per run from the input directory and writes, for every
trophy in the case, five quantiles (5/25/50/75/95%) of its Exophase earn
rate at the case's target time.

Model
-----
An earn rate is a ratio: earners / players_tracked. The page's history lets
us observe both parts separately, and they move for different reasons:

* players_tracked grows as Exophase discovers more of the player base; the
  growth is fast right after launch and decays as the tracked pool matures.
* earners of a trophy only ever grow, quickly at first (everyone passes the
  early story beats together) and then more slowly as the remaining pool of
  players who will ever earn it thins out.

So we extrapolate the two series independently with damped log-growth
(slopes estimated from the recent part of the recorded history, the damping
time constant reflecting that both processes saturate), then divide.
Per-trophy earner growth is noisy when a trophy has few earners, so each
trophy's slope is shrunk toward the pooled slope of all trophies, weighted
by its earner count.

The spread around the median is set on the logit scale (the scale the
contest scores on) and combines three terms: a base term that grows with
the square root of the horizon, a counting term ~1/sqrt(earners) for the
sampling noise of small counts, and a disagreement term proportional to how
far the median itself moved from the last observed rate.

Deterministic, standard library only, runs in well under a second.
"""

import json
import math
import os
from pathlib import Path

INPUT_DIR = Path(os.environ.get("MAKEFUN_INPUT_DIR", "/input"))
OUTPUT_DIR = Path(os.environ.get("MAKEFUN_OUTPUT_DIR", "/output"))

Z = [-1.6448536269514722, -0.6744897501960817, 0.0,
     0.6744897501960817, 1.6448536269514722]

DAMP_DAYS = 1.2        # growth-rate decay time constant, days
RECENT_HOURS = 12.0    # window for "recent" slope estimation
BASE_SIGMA = 0.30      # logit spread floor
HORIZON_SIGMA = 0.24   # extra logit spread per sqrt(day of horizon)
DISAGREE = 0.25        # spread per unit of median logit movement
MAX_SIGMA = 1.7


def clamp(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


def logit(p):
    p = clamp(p, 0.0001, 0.9999)
    return math.log(p / (1.0 - p))


def inv_logit(x):
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


def parse_time(s):
    # ISO-8601 UTC like 2026-11-19T03:00:00Z (fraction optional)
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1]
    frac = 0.0
    if "." in s:
        s, f = s.split(".", 1)
        frac = float("0." + f)
    date, _, tod = s.partition("T")
    y, mo, d = (int(v) for v in date.split("-"))
    hh, mm, ss = (int(v) for v in tod.split(":"))
    # days since civil epoch (Howard Hinnant's algorithm), no datetime needed
    y_adj = y - (1 if mo <= 2 else 0)
    era = (y_adj if y_adj >= 0 else y_adj - 399) // 400
    yoe = y_adj - era * 400
    mp = (mo + 9) % 12
    doy = (153 * mp + 2) // 5 + d - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    days = era * 146097 + doe - 719468
    return ((days * 24 + hh) * 60 + mm) * 60 + ss + frac


def slope(xs, ys):
    """Least-squares slope of ys on xs; 0.0 when it cannot be estimated."""
    n = len(xs)
    if n < 2:
        return 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return 0.0
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx


def damped_growth(rate_per_day, days):
    """Integral of a growth rate that decays with time constant DAMP_DAYS."""
    if days <= 0:
        return 0.0
    return rate_per_day * DAMP_DAYS * (1.0 - math.exp(-days / DAMP_DAYS))


def refresh_series(history):
    """Collapse the raw reads to the moments the page actually changed.

    Between Exophase refreshes every read is identical, and identical reads
    carry no extra information about dynamics; keeping one copy of each
    distinct page state keeps slope estimates honest.
    """
    kept = []
    for read in history:
        key = (read.get("players_tracked"), tuple(
            -1.0 if r is None else r for r in read.get("rates", [])))
        if kept and kept[-1][0] == key:
            continue
        kept.append((key, read))
    return [read for _, read in kept]


def forecast_case(case):
    trophies = case["trophies"]
    history = case["history"]
    if not history:
        raise ValueError("case has no history")
    reads = refresh_series(history)
    t0 = parse_time(reads[0]["slot"])
    times = [(parse_time(r["slot"]) - t0) / 3600.0 for r in reads]  # hours
    horizon_days = float(case["horizon_minutes"]) / 1440.0

    # Launch-window prior: if the recorded history holds at most two distinct
    # page states, the snapshot is a frozen pre-refresh page from the launch
    # rush. The tracked pool is about to catch up explosively, which dilutes
    # every rate even as earners grow, so medians shift down on the logit
    # scale and the spread widens to match that regime's real uncertainty.
    launch_frozen = len(reads) <= 2

    # Staleness of the final state: how long the page had been unchanged at
    # the checkpoint, relative to the usual gap between refreshes. A stale
    # page hides movement the forecast should price in as extra spread.
    stale_extra = 0.0
    if len(times) >= 3:
        gaps = sorted(b - a for a, b in zip(times, times[1:]))
        median_gap = gaps[len(gaps) // 2]
        age = (parse_time(case["at"]) - parse_time(reads[-1]["slot"])) / 3600.0
        if median_gap > 0:
            stale_ratio = age / median_gap
            if stale_ratio > 1.0:
                stale_extra = clamp(0.12 * (stale_ratio - 1.0), 0.0, 0.35)

    players = [float(r["players_tracked"]) for r in reads]
    p_last = players[-1]

    # --- players-tracked extrapolation -------------------------------------
    def window_slope(series, transform):
        pts = [(t, transform(v)) for t, v in zip(times, series)]
        recent = [(t, v) for t, v in pts if t >= times[-1] - RECENT_HOURS]
        g_recent = slope([t for t, _ in recent], [v for _, v in recent])
        g_all = slope([t for t, _ in pts], [v for _, v in pts])
        if len(recent) >= 2 and times[-1] - recent[0][0] > 1.0:
            return 0.7 * g_recent + 0.3 * g_all
        return g_all

    g_players = window_slope(players, math.log) * 24.0  # per day
    g_players = clamp(g_players, -0.05, 1.2)
    p_growth = clamp(damped_growth(g_players, horizon_days), -0.1, 1.6)
    p_target = p_last * math.exp(p_growth)
    p_target = max(p_target, p_last * 0.9, 1.0)

    # --- earners per trophy -------------------------------------------------
    n = len(trophies)
    earners = []          # per trophy: list over reads (floats, monotone)
    for i in range(n):
        col = []
        for read, p in zip(reads, players):
            r = read["rates"][i] if i < len(read.get("rates", [])) else None
            col.append(None if r is None else float(r) / 100.0 * p)
        # fill gaps by carrying the last observation, then enforce monotone
        filled, last_v = [], 0.0
        for v in col:
            if v is not None:
                last_v = v
            filled.append(last_v)
        mono, run = [], 0.0
        for v in filled:
            run = max(run, v)
            mono.append(run)
        earners.append(mono)

    # pooled earner growth (log slope per day), the shrinkage target;
    # estimated over the same recent window as the per-trophy slopes
    span_all = times[-1] - times[0] if times else 0.0
    win_all = min(12.0, max(span_all / 2.0, 1.0))
    pooled_pts_t, pooled_pts_y = [], []
    for col in earners:
        for t, e in zip(times, col):
            if t >= times[-1] - win_all:
                pooled_pts_t.append(t)
                pooled_pts_y.append(math.log(e + 25.0))
    g_pool = slope(pooled_pts_t, pooled_pts_y) * 24.0
    g_pool = clamp(g_pool, 0.0, 1.0)

    rows = []
    for i, trophy in enumerate(trophies):
        col = earners[i]
        e_last = col[-1]
        last_rate = None
        for read in reversed(history):
            if i < len(read.get("rates", [])) and read["rates"][i] is not None:
                last_rate = float(read["rates"][i])
                break
        if last_rate is None:
            last_rate = 100.0 * e_last / p_last if p_last else 0.0

        if e_last >= 0.999 * p_last:
            # effectively everyone tracked has it; it stays pinned near 100
            median, sigma = 99.95, 0.06
        else:
            # Earner growth from the recent window only (at most the last
            # day, or the latter half of a shorter history): the launch
            # ramp at the start of the series would otherwise swamp the
            # slope and project absurd growth over a multi-day horizon.
            span = times[-1] - times[0] if times else 0.0
            win = min(12.0, max(span / 2.0, 1.0))
            pts = [(t, math.log(e + 25.0)) for t, e in zip(times, col)]
            recent = [(t, v) for t, v in pts if t >= times[-1] - win]
            g_own = slope([t for t, _ in recent], [v for _, v in recent]) * 24.0
            if len(recent) < 2:
                g_own = g_pool
            g_own = clamp(g_own, 0.0, 1.5)
            w = e_last / (e_last + 300.0)  # trust own slope when counts are big
            g_e = w * g_own + (1.0 - w) * g_pool
            e_growth = clamp(damped_growth(g_e, horizon_days), 0.0, 1.0)
            e_target = e_last * math.exp(e_growth)
            e_target = clamp(e_target, e_last, 0.9995 * p_target)
            median = clamp(100.0 * e_target / p_target, 0.02, 99.98)
            moved = abs(logit(median / 100.0) - logit(last_rate / 100.0))
            sigma = math.sqrt(
                BASE_SIGMA ** 2
                + (HORIZON_SIGMA * math.sqrt(horizon_days)) ** 2
                + 1.0 / max(e_target, 4.0)
                + (DISAGREE * moved) ** 2
            )
            sigma = clamp(sigma, 0.18, MAX_SIGMA)
            if launch_frozen:
                median = 100.0 * inv_logit(logit(median / 100.0) - 0.6)
                sigma = max(sigma, 0.85)
            elif stale_extra:
                sigma = clamp(sigma + stale_extra, 0.18, MAX_SIGMA)

        centre = logit(median / 100.0)
        qs = [round(clamp(100.0 * inv_logit(centre + z * sigma), 0.0, 100.0), 4)
              for z in Z]
        for k in range(1, len(qs)):  # keep the row non-decreasing
            if qs[k] < qs[k - 1]:
                qs[k] = qs[k - 1]
        rows.append({"id": trophy["id"], "q": qs})
    return {"trophies": rows}


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for path in sorted(INPUT_DIR.glob("*.in")):
        case = json.loads(path.read_text(encoding="utf-8"))
        result = forecast_case(case)
        out_path = OUTPUT_DIR / (path.stem + ".out")
        out_path.write_text(json.dumps(result), encoding="utf-8")


if __name__ == "__main__":
    main()
