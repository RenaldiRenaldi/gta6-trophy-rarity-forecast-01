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
    if desc.startswith("complete ") and "final mission for" not in desc:
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


def robust_line(points, target, window=None):
    chosen = points if window is None else [p for p in points if p[0] >= points[-1][0] - window]
    if len(chosen) < 3:
        chosen = points
    xs = [math.log1p(max(0, t)) for t, _ in chosen]
    ys = [logodds(r) for _, r in chosen]
    weights = [1.0] * len(chosen)
    for _ in range(3):
        total = sum(weights)
        mx = sum(w * x for w, x in zip(weights, xs)) / total
        my = sum(w * y for w, y in zip(weights, ys)) / total
        info = sum(w * (x - mx) ** 2 for w, x in zip(weights, xs))
        slope = sum(w * (x - mx) * (y - my) for w, x, y in zip(weights, xs, ys)) / max(info, 1e-8)
        intercept = my - slope * mx
        errors = [y - intercept - slope * x for x, y in zip(xs, ys)]
        scale = max(0.10, 1.4826 * median([abs(e) for e in errors]))
        weights = [min(1.0, 1.5 * scale / max(abs(e), 1e-8)) for e in errors]
    residual = math.sqrt(sum(e * e for e in errors) / max(1, len(errors) - 2))
    anchor = 0.8 * ys[-1] + 0.2 * (intercept + slope * xs[-1])
    distance = max(0, math.log1p(max(0, target)) - xs[-1])
    return anchor, slope, residual, distance, info


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


def launch_refresh(last, group, days, population):
    # RULES documents a roughly sixfold first refresh, not a trophy answer table.
    dilution = min(0.65, max(0.08, population / 3868))
    center = logodds(last * dilution)
    spread = math.hypot(0.58, 0.5 * math.sqrt(days))
    if group == "platinum":
        center -= 0.12
        spread = math.hypot(spread, 0.25)
    elif group == "story":
        center += 0.22
    elif group == "optional":
        center += 0.15
    if group == "intro" and last >= 99.9:
        return [round(percent(logodds(last) + z * spread), 6) for z in ZS[:4]] + [100.0]
    return mixture_quantiles(((0.94, center, spread), (0.06, logodds(last), 0.25)))


def forecast(case):
    target, checkpoint = hours(case["target"]), hours(case["at"])
    days = max(0, (target - checkpoint) / 24)
    rw = 0.5 * math.sqrt(days)
    history, epochs, rows = case.get("history", []), snapshots(case), []
    for index, trophy in enumerate(case["trophies"]):
        available = [r for r in history if index < len(r["rates"]) and r["rates"][index] is not None]
        group = kind(trophy)
        last = float(available[-1]["rates"][index]) if available else 0.01
        if len(available) < 3:
            q = [round(percent(logodds(last) + z * rw), 6) for z in ZS]
            rows.append({"id": trophy["id"], "q": q})
            continue
        population = max(1, available[-1].get("players_tracked", 1))
        if len(epochs) == 1 and checkpoint <= 12 and population < 1500:
            q = launch_refresh(last, group, days, population)
        else:
            points = [(t, float(r["rates"][index])) for t, r in epochs if index < len(r["rates"]) and r["rates"][index] is not None]
            cut = None
            for j in range(1, len(epochs)):
                before = epochs[j - 1][1].get("players_tracked", 1)
                after = epochs[j][1].get("players_tracked", 1)
                if epochs[j][0] < 12 and after > 2 * max(1, before):
                    cut = epochs[j][0]
            if cut is not None:
                points = [p for p in points if p[0] >= cut]
            if len(points) < 3 or points[-1][0] - points[0][0] < 1:
                q = mixture_quantiles(((1, logodds(last), math.hypot(rw, 0.45)),))
            else:
                components = []
                for weight, fit in ((0.55, robust_line(points, target)), (0.45, robust_line(points, target, 24))):
                    anchor, slope, residual, distance, information = fit
                    beta = max(-1.5, min(5.0, slope)) * information / (information + 0.025)
                    if group == "platinum":
                        beta = max(-0.8, min(2.2, beta))
                    growth = beta * distance
                    sigma_beta = max(0.12, residual / math.sqrt(max(information, 0.04)))
                    noise = min(0.60, 1 / math.sqrt(max(1, population * last / 100)))
                    spread = math.sqrt(rw * rw + residual ** 2 + (distance * sigma_beta) ** 2 + noise ** 2)
                    if group in ("optional", "completion", "platinum") and growth > 0:
                        ceiling = min(99.99, max({"optional": 65.0, "completion": 35.0, "platinum": 8.0}[group], last * 1.4))
                        scaled = min(99.99, 100 * last / ceiling)
                        saturated = ceiling * percent(logodds(scaled) + growth) / 100
                        components.append((weight * 0.35, logodds(saturated), spread))
                        weight *= 0.65
                    components.append((weight, anchor + growth, spread))
                q = mixture_quantiles(components)
        if group == "intro" and last >= 99.99:
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
