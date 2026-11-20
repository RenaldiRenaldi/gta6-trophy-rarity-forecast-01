"""Refresh-aware trophy forecasts; Python standard library, deterministic output."""

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from statistics import NormalDist, median

LEVELS = (0.05, 0.25, 0.5, 0.75, 0.95)
NORMAL = NormalDist()
ZS = tuple(NORMAL.inv_cdf(p) for p in LEVELS)
LAUNCH = datetime(2026, 11, 19, tzinfo=timezone.utc).timestamp()


def hours(stamp):
    return (datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp() - LAUNCH) / 3600


def logodds(rate):
    p = min(0.9999, max(0.0001, rate / 100))
    return math.log(p / (1 - p))


def percent(z):
    return 100 / (1 + math.exp(-min(35, max(-35, z))))


def kind(trophy):
    desc = trophy.get("description", "").lower()
    if trophy.get("tier") == "platinum":
        return "platinum"
    if "gone fishing" in desc:
        return "intro"
    if "chapter" in desc or "complete the final mission." in desc:
        return "story"
    if "100%" in desc or trophy.get("tier") in ("silver", "gold"):
        return "completion"
    if trophy.get("secret") and desc.startswith("complete ") and "final mission for" not in desc:
        return "story"
    return "optional"


def snapshots(case):
    # Repeated recorder reads are one observation, not independent evidence.
    result, previous = [], None
    for row in sorted(case.get("history", []), key=lambda r: r["at"]):
        signature = (row.get("players_tracked"), tuple(row["rates"]))
        if signature != previous:
            result.append((hours(row["at"]), row))
            previous = signature
    return result


def line_fit(xs, ys, variances):
    """Robust precision-weighted regression, including rounded-count uncertainty."""
    weights = [1 / max(0.0025, v) for v in variances]
    for _ in range(2):
        total = sum(weights)
        mx = sum(w * x for w, x in zip(weights, xs)) / total
        my = sum(w * y for w, y in zip(weights, ys)) / total
        info = sum(w * (x - mx) ** 2 for w, x in zip(weights, xs))
        beta = sum(w * (x - mx) * (y - my) for w, x, y in zip(weights, xs, ys)) / max(info, 1e-9)
        intercept = my - beta * mx
        errors = [y - intercept - beta * x for x, y in zip(xs, ys)]
        scale = max(0.05, 1.4826 * median([abs(e) for e in errors]))
        weights = [min(1, 1.5 * scale / max(abs(e), 1e-9)) / max(0.0025, v)
                   for e, v in zip(errors, variances)]
    residual = sum(e * e for e in errors) / max(1, len(xs) - 2)
    variance = max(1, sum(w * e * e for w, e in zip(weights, errors)) / max(1, len(xs) - 2)) / max(info, 1e-9)
    return intercept, beta, residual, variance


# The documented first refresh is our only empirical shock-scale anchor.
FIRST_JUMP = 3868 / 638
DIFFUSION = math.log(FIRST_JUMP) / math.sqrt(24 * 30)
CEILINGS = {"intro": 1.0, "story": 0.98, "optional": 0.65,
            "completion": 0.35, "platinum": 0.08}
SHRINKAGE = False  # The rolling holdout did not establish no-regression.
LARGE_LATER_REFRESH = math.log(1.5)
LAUNCH_GROWTH = {"intro": 0.16, "story": 0.22, "optional": 0.19,
                 "completion": 0.17, "platinum": 0.15}


def player_path(epochs, target):
    """One log-time player trajectory with arbitrary-time observed level steps."""
    if not epochs:
        return 1.0, 0.0, 0.0, []
    xs = [math.log1p(max(0, t)) for t, _ in epochs]
    ps = [max(1, r.get("players_tracked", 1)) for _, r in epochs]
    increments = [(ps[i] - ps[i - 1]) / (xs[i] - xs[i - 1])
                  for i in range(1, len(ps)) if xs[i] > xs[i - 1]]
    slope = max(0, median(increments)) if increments else 0
    steps, organic = [], []
    offset = 0
    for i, p in enumerate(ps):
        if i:
            excess = p - ps[i - 1] - slope * (xs[i] - xs[i - 1])
            threshold = 4 * math.sqrt(ps[i - 1])
            if abs(excess) > threshold:
                offset += excess
                steps.append((epochs[i][0], excess, math.log(p / ps[i - 1])))
        organic.append(p - offset)
    if len(ps) >= 3:
        _, slope, _, _ = line_fit(xs, organic, [max(1, p) for p in ps])
        slope = max(0, slope)
    distance = max(0, math.log1p(max(0, target)) - xs[-1])
    smooth = max(ps[-1], ps[-1] + slope * distance)
    # The exceptional sixfold first sync is not treated as a daily event.
    later = [s for s in steps if s[2] < math.log(3)]
    span = max(24, epochs[-1][0] - (steps[0][0] if steps else epochs[0][0]))
    # Refreshes bring additive cohorts. Estimate their count/time intensity after
    # the exceptional first sync, instead of repeatedly multiplying by six.
    age = max(24, epochs[-1][0] + 24)
    exposure = age * math.log1p(max(0, target - epochs[-1][0]) / age)
    additions = 0.8 * sum(max(0, s[1]) for s in later) * exposure / span
    forecast = smooth + additions
    future_step = math.log(forecast / smooth)
    return forecast, math.log(forecast / ps[-1]), future_step, steps


def earner_fits(points, group, population, target, ceiling_mix=True):
    """Fit continuous earner counts; refresh observations are never discarded."""
    maximum = max(e for _, e, _ in points)
    base = population * CEILINGS[group]
    capacities = [max(maximum * 1.02 + 1, base * factor) for factor in (0.55, 1, 1.8)]
    if not ceiling_mix:
        capacities = [capacities[1]]
    candidates = []
    for window in (None, 24):
        selected = points if window is None else [p for p in points if p[0] >= points[-1][0] - window]
        if len(selected) < 5:
            continue
        for delay in (0, 12, 24):
            if delay and any(e > max(2, 3 * math.sqrt(max(1, e))) for t, e, _ in points if t <= delay):
                continue
            active = [p for p in selected if p[0] > delay and p[1] < 0.99995 * p[2]]
            if len(active) < 5:
                continue
            for cap in capacities:
                xs, ys, variances = [], [], []
                for t, e, p in active:
                    count = max(0.25, min(cap - 0.25, e))
                    derivative = 1 / count + 1 / (cap - count)
                    # Binomial cohort noise plus independent two-decimal rounding.
                    variance = (max(0.25, e * (1 - min(1, e / p))) + (p * 0.00005) ** 2 / 3) * derivative ** 2
                    xs.append(math.log1p(t - delay))
                    ys.append(math.log(count / (cap - count)))
                    variances.append(variance)
                a, b, residual, vb = line_fit(xs, ys, variances)
                # Compare every candidate on the same full history in count
                # space. A recent window must explain the held-out early data;
                # it cannot win simply by fitting fewer points. Printed 100%
                # is censored: larger latent earning capacity is not an error.
                errors = []
                for t, e, p in points:
                    if t <= delay:
                        predicted = 0.0
                    else:
                        predicted = cap * percent(a + max(0, b) * math.log1p(t - delay)) / 100
                    error = math.log((predicted + 0.5) / (e + 0.5))
                    if e >= 0.99995 * p:
                        error = min(0, error)
                    precision = e / (e + 4)
                    errors.append(precision * min(4, error * error))
                score = sum(errors) / len(errors) + 0.0006 * (delay > 0)
                candidates.append({"cap": cap, "delay": delay, "a": a,
                                   "b": max(0, min(5, b)), "residual": residual,
                                   "vb": vb, "score": score, "x": xs[-1],
                                   "data": list(zip(xs, ys, variances, [p for _, _, p in active])),
                                   "distance": max(0, math.log1p(max(0, target - delay)) - xs[-1])})
    if not candidates:
        return []
    candidates.sort(key=lambda c: (c["score"], c["cap"], c["delay"]))
    # Retain competing ceilings, rather than three nearly identical window fits.
    best = []
    for cap in sorted(set(c["cap"] for c in candidates)):
        best.append(next(c for c in candidates if c["cap"] == cap))
    floor = min(c["score"] for c in best)
    for c in best:
        c["weight"] = math.exp(-min(30, (c["score"] - floor) / 0.006))
    return best


def group_priors(fits, trophies):
    priors = {}
    for group in CEILINGS:
        for delay in (0, 12, 24):
            selected = []
            for fs, trophy in zip(fits, trophies):
                eligible = [c for c in fs if c["delay"] == delay]
                if kind(trophy) == group and eligible:
                    selected.append(min(eligible, key=lambda c: c["score"]))
            if selected:
                # Learn the latent shape from each trophy's best ceiling fit.
                # Pooling deliberately wrong ceiling transforms biases a prior.
                weights = [1 / (0.25 + c["vb"]) for c in selected]
                mean = sum(w * c["b"] for w, c in zip(weights, selected)) / sum(weights)
                between = sum(w * (c["b"] - mean) ** 2 for w, c in zip(weights, selected)) / sum(weights)
                # Choose pooling strength on a rolling holdout contained wholly
                # inside the observed history. Group similarity is not itself
                # proof that shrinking improves out-of-time predictions.
                validation = []
                for c in selected:
                    data = c["data"]
                    cut = max(3, len(data) - max(3, len(data) // 4))
                    if cut >= len(data):
                        continue
                    xs, ys, vs, _ = zip(*data[:cut])
                    a, b, _, vb = line_fit(xs, ys, vs)
                    validation.append((c, a + b * xs[-1], b, vb, xs[-1], data[cut:]))
                errors = []
                for strength in (0, 0.5, 1):
                    score = 0.0
                    for c, anchor, b, vb, x, heldout in validation:
                        peers_b = [v[2] for v in validation if v[0] is not c]
                        prior = sum(peers_b) / len(peers_b) if peers_b else b
                        fraction = strength * vb / (max(0.04, between) + vb)
                        slope = (1 - fraction) * b + fraction * prior
                        for xx, yy, _, population in heldout:
                            predicted = max(1e-8, c["cap"] * percent(anchor + slope * (xx - x)) / 100)
                            observed = max(1e-8, c["cap"] * percent(yy) / 100)
                            score += abs(logodds(100 * predicted / population)
                                         - logodds(100 * observed / population)) / len(heldout)
                    errors.append((score, strength))
                strength = min(errors)[1]
                if min(errors)[0] >= errors[0][0] * 0.995:
                    strength = 0
                priors[group, delay] = (mean, max(0.04, between), len(selected), strength)
    return priors


def mixture_quantiles(components):
    """Invert a mixture CDF rather than average incompatible quantiles."""
    total = sum(w for w, _, _ in components)
    result = []
    for level in LEVELS:
        lo, hi = -22.0, 22.0
        for _ in range(50):
            mid = (lo + hi) / 2
            cdf = sum(w * NORMAL.cdf((mid - m) / max(s, 0.015)) for w, m, s in components) / total
            if cdf < level:
                lo = mid
            else:
                hi = mid
        result.append(round(percent((lo + hi) / 2), 6))
    return result


def count_quantiles(components):
    """Log-count-ratio mixture, with the physical 100% boundary as an atom.

    Transforming after inversion propagates denominator risk even when a point
    count forecast exceeds P; a clipped logit center would erase that risk.
    """
    total = sum(w for w, _, _ in components)
    result = []
    for level in LEVELS:
        lo, hi = -25.0, 8.0
        for _ in range(45):
            mid = (lo + hi) / 2
            cdf = sum(w * NORMAL.cdf((mid - m) / max(0.02, s)) for w, m, s in components) / total
            if cdf < level:
                lo = mid
            else:
                hi = mid
        result.append(round(min(100, 100 * math.exp((lo + hi) / 2)), 6))
    return result


def forecast_regime(epochs, horizon_minutes):
    if len(epochs) == 1 and max(1, epochs[0][1].get("players_tracked", 1)) < 1500:
        launch_window = epochs[0][0] <= 6
        return "launch_refresh" if launch_window and horizon_minutes == 90 else "uncertain_refresh"
    return "trend"


def later_refresh_scale(steps):
    # Match the player path's exclusion of launch-sized syncs. A smaller
    # first detected jump can still be evidence in a partial history.
    return sum(log_step for _, excess, log_step in steps
               if excess > 0 and 0 < log_step < math.log(3))


def launch_refresh(last, group, days, population, calibrated):
    # RULES documents a roughly sixfold first refresh, not a trophy answer table.
    dilution = min(0.65, max(0.08, population / 3868))
    center = logodds(last * dilution)
    diffusion = DIFFUSION * math.sqrt(days)
    if group == "intro" and last >= 99.9:
        spread = math.hypot(0.440, diffusion)
        return [round(percent(logodds(last) + z * spread), 6) for z in ZS[:4]] + [100.0]
    if calibrated:
        center += LAUNCH_GROWTH[group]
        spread = math.hypot(0.075, diffusion)
    else:
        spread = math.hypot(0.440, diffusion)
        center += {"platinum": -0.12, "story": 0.22, "optional": 0.15}.get(group, 0)
        old_spread = math.hypot(0.50, 0.5 * math.sqrt(days))
        if group == "platinum":
            spread = math.hypot(spread, 0.25)
            old_spread = math.hypot(old_spread, 0.25)
        center += old_spread * NORMAL.inv_cdf(0.5 / 0.94)
    result = mixture_quantiles(((1, center, spread),))
    # The scored variable is the printed two-decimal rate. In particular its
    # zero atom must be represented even when the latent earn rate is positive.
    result[0] = min(result[1], round(result[0], 2))
    result[4] = max(result[3], round(result[4], 2))
    return result


def forecast(case, *, shrink=None, ceiling_mix=True, asymmetric=None):
    shrink = SHRINKAGE if shrink is None else shrink
    target, checkpoint = hours(case["target"]), hours(case["at"])
    days = max(0, (target - checkpoint) / 24)
    rw = DIFFUSION * math.sqrt(days)
    history, epochs, rows = case.get("history", []), snapshots(case), []
    regime = forecast_regime(epochs, round((target - checkpoint) * 60))
    population_future, player_growth, step_risk, steps = player_path(epochs, target)
    refresh_scale = later_refresh_scale(steps)
    use_asymmetry = (regime == "trend" and asymmetric is not False
                     and refresh_scale >= LARGE_LATER_REFRESH)
    fits = []
    for index, trophy in enumerate(case["trophies"]):
        points = [(t, float(r["rates"][index]) * max(1, r.get("players_tracked", 1)) / 100,
                   max(1, r.get("players_tracked", 1))) for t, r in epochs
                  if index < len(r["rates"]) and r["rates"][index] is not None]
        fits.append(earner_fits(points, kind(trophy), points[-1][2], target, ceiling_mix)
                    if len(points) >= 5 else [])
    priors = group_priors(fits, case["trophies"]) if shrink else {}
    for index, trophy in enumerate(case["trophies"]):
        available = [r for r in history if index < len(r["rates"]) and r["rates"][index] is not None]
        group = kind(trophy)
        last = float(available[-1]["rates"][index]) if available else 0.01
        if len(available) < 3:
            sparse_spread = 0.5 * math.sqrt(days)
            q = [round(percent(logodds(last) + z * sparse_spread), 4) for z in (-1.645, -0.674, 0, 0.674, 1.645)]
            rows.append({"id": trophy["id"], "q": q})
            continue
        population = max(1, available[-1].get("players_tracked", 1))
        if regime != "trend":
            q = launch_refresh(last, group, days, population, regime == "launch_refresh")
        else:
            candidates = fits[index]
            if not candidates:
                q = mixture_quantiles(((1, logodds(last), math.hypot(rw, 0.45)),))
            else:
                components = []
                for fit in candidates:
                    # A rolling observed-history holdout gates kind pooling.
                    mean, between, peers, strength = priors.get(
                        (group, fit["delay"]), (fit["b"], max(0.04, fit["vb"]), 1, 0))
                    pooling = strength * (peers - 1) / (peers + 3) if shrink else 0
                    reliability = 1 - pooling * fit["vb"] / (between + fit["vb"])
                    beta = reliability * fit["b"] + (1 - reliability) * mean
                    anchor = fit["a"] + fit["b"] * fit["x"]
                    count = fit["cap"] * percent(anchor + beta * fit["distance"]) / 100
                    center = math.log(max(1e-10, count / population_future))
                    sensitivity = max(0.05, 1 - count / fit["cap"])
                    # Pool the slope mean without also pretending that shared
                    # cohort/model uncertainty disappeared through pooling.
                    sigma_beta = math.sqrt(fit["vb"]) * sensitivity
                    refresh_uncertainty = 0.10 * math.sqrt(days) + 0.25 * player_growth
                    spread = math.sqrt(0.025 ** 2 + rw ** 2 + fit["residual"] * sensitivity ** 2
                                       + (fit["distance"] * sigma_beta) ** 2
                                       + refresh_uncertainty ** 2)
                    weight = fit["weight"]
                    if pooling:
                        # A hierarchical shape prior updates ceiling evidence as
                        # well as the conditional slope mean. Otherwise a poorly
                        # identified ceiling can retain an incompatible slope.
                        prior_variance = between + fit["vb"]
                        weight *= math.exp(-0.5 * pooling * (fit["b"] - mean) ** 2 / prior_variance)
                        weight *= (between / prior_variance) ** (0.5 * pooling)
                    if last <= 0.02 and days >= 1 and group != "intro":
                        components.append((weight * 0.15, center + 1.0, math.hypot(spread, 0.4)))
                        weight *= 0.85
                    components.append((weight, center, spread))
                q = count_quantiles(components)
                if use_asymmetry:
                    # Extra lower-tail dilution scales with expected denominator
                    # growth, conserving the fitted median and quantile order.
                    risk = min(0.65, 0.35 * player_growth + step_risk)
                    q = [min(v, round(percent(logodds(v) - abs(z) * risk), 6)) if z < 0 else v
                         for z, v in zip(ZS, q)]
        # Coverage uses raw percentages even though scoring clips endpoints.
        if last == 0:
            q[0] = 0.0
        if last >= 99.99:
            q[-1] = 100.0
        rows.append({"id": trophy["id"], "q": q})
    others = [r for r, t in zip(rows, case["trophies"]) if t.get("tier") != "platinum"]
    for row, trophy in zip(rows, case["trophies"]):
        if trophy.get("tier") == "platinum" and others:
            row["q"] = [min(row["q"][j], min(r["q"][j] for r in others)) for j in range(5)]
    return {"trophies": rows}


def main():
    source = Path(os.environ.get("MAKEFUN_INPUT_DIR", "/input"))
    destination = Path(os.environ.get("MAKEFUN_OUTPUT_DIR", "/output"))
    destination.mkdir(parents=True, exist_ok=True)
    for path in sorted(source.glob("*.in")):
        result = forecast(json.loads(path.read_text(encoding="utf-8")))
        payload = json.dumps(result, separators=(",", ":"), allow_nan=False) + "\n"
        if len(payload.encode("utf-8")) > 65536:
            raise ValueError("forecast exceeds the competition byte limit")
        (destination / (path.stem + ".out")).write_text(payload, encoding="utf-8")


if __name__ == "__main__":
    main()
