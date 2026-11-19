"""Validity, deterministic execution, and explicitly synthetic forecast diagnostics."""

import argparse
import copy
import json
import math
import os
import random
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import main


def baseline(case):
    spread = 0.5 * math.sqrt(case["horizon_minutes"] / 1440)
    return {"trophies": [
        {"id": trophy["id"], "q": [round(main.percent(main.logodds(rate) + z * spread), 4) for z in (-1.645, -0.674, 0, 0.674, 1.645)]}
        for trophy, rate in zip(case["trophies"], case["history"][-1]["rates"])
    ]}


def check_shape(case, output):
    assert set(output) == {"trophies"}
    rows = output["trophies"]
    expected = {t["id"] for t in case["trophies"]}
    assert len(rows) == len(expected)
    assert {r["id"] for r in rows} == expected
    for row in rows:
        assert set(row) == {"id", "q"}
        assert len(row["q"]) == 5
        assert all(type(q) in (float, int) and math.isfinite(q) and 0 <= q <= 100 for q in row["q"])
        assert row["q"] == sorted(row["q"])
    assert len(json.dumps(output).encode()) <= 65536


def metrics(pairs):
    losses, misses, covered = [], [], []
    for output, actual in pairs:
        for row in output["trophies"]:
            y = actual[row["id"]]
            misses.append(abs(y - row["q"][2]))
            covered.append(row["q"][0] <= y <= row["q"][-1])
            for level, q in zip(main.LEVELS, row["q"]):
                delta = main.logodds(y) - main.logodds(q)
                losses.append(2 * max(level * delta, (level - 1) * delta))
    return {"loss": round(sum(losses) / len(losses), 6),
            "typical_miss_pp": round(sum(misses) / len(misses), 6),
            "coverage_90": round(sum(covered) / len(covered), 6),
            "trophies_scored": len(misses)}


def stamp(hour):
    start = datetime(2026, 11, 19, tzinfo=timezone.utc)
    return (start + timedelta(hours=hour)).isoformat().replace("+00:00", "Z")


def synthetic_refresh(cases):
    # These are hypothetical answers, never the hidden practice reference.
    rng = random.Random(81721)
    pairs, house = [], []
    for trial in range(60):
        case = cases[trial % len(cases)]
        actual = {}
        ratio = math.exp(rng.gauss(math.log(0.21), 0.40))
        for trophy, rate in zip(case["trophies"], case["history"][-1]["rates"]):
            if main.kind(trophy) == "intro":
                y = 100.0
            else:
                multiplier = math.exp(rng.gauss(0, 0.35))
                y = round(min(100, max(0, rate * ratio * multiplier)), 2)
            actual[trophy["id"]] = y
        pairs.append((main.forecast(case), actual))
        house.append((baseline(case), actual))
    return {"model": metrics(pairs), "baseline": metrics(house)}


def synthetic_growth(template):
    # Full-history, late-growth, and saturating curves exercise final-only paths.
    rng = random.Random(250119)
    pairs, house = [], []
    for trial in range(36):
        case = copy.deepcopy(template)
        case.update(at=stamp(59.5), target=stamp(168), horizon_minutes=6510)
        curves = []
        for trophy in case["trophies"]:
            group = main.kind(trophy)
            ceiling = {"intro": 100, "story": 98, "optional": 65, "completion": 35, "platinum": 8}[group]
            if group == "intro":
                intercept, slope = 0.5, 1.7
            else:
                intercept = rng.uniform(-12, -6)
                slope = rng.uniform(0.45, 2.1)
            curves.append((ceiling, intercept, slope))

        def rates(hour, noisy=False):
            result = []
            for ceiling, intercept, slope in curves:
                z = intercept + slope * math.log1p(max(0, hour))
                if noisy:
                    z += rng.gauss(0, 0.06)
                result.append(round(ceiling * main.percent(z) / 100, 2))
            return result

        case["history"] = []
        for hour in range(5, 60, 2):
            current = rates(hour, True)
            row = {"at": stamp(hour), "slot": stamp(hour), "players_tracked": 4000 + hour * 1000, "rates": current}
            case["history"].append(row)
            stale = copy.deepcopy(row)
            stale["at"] = stamp(hour + 0.5)
            case["history"].append(stale)
        actual_rates = rates(168)
        # The future is not guaranteed to stay on a noiseless fitted curve.
        actual = {t["id"]: round(main.percent(main.logodds(y) + rng.gauss(0, 0.50)), 2) for t, y in zip(case["trophies"], actual_rates)}
        output = main.forecast(case)
        check_shape(case, output)
        pairs.append((output, actual))
        house.append((baseline(case), actual))
    return {"model": metrics(pairs), "baseline": metrics(house)}


def execute_cases(input_dir, cases):
    with tempfile.TemporaryDirectory(prefix="trophy-check-") as temp:
        env = dict(os.environ, MAKEFUN_INPUT_DIR=str(input_dir), MAKEFUN_OUTPUT_DIR=temp, PYTHONDONTWRITEBYTECODE="1")
        command = [sys.executable, "-I", str(Path(__file__).with_name("main.py"))]
        started = time.perf_counter()
        subprocess.run(command, check=True, env=env, timeout=8)
        elapsed = time.perf_counter() - started
        first = {p.name: p.read_bytes() for p in Path(temp).glob("*.out")}
        subprocess.run(command, check=True, env=env, timeout=8)
        second = {p.name: p.read_bytes() for p in Path(temp).glob("*.out")}
        assert first == second, "output differs across executions"
        assert len(first) == len(cases)
        for case in cases:
            output = json.loads(first[case["id"] + ".out"])
            check_shape(case, output)
        return {"valid_cases": len(cases), "byte_identical": True, "elapsed_seconds": round(elapsed, 4), "output_bytes": {name: len(data) for name, data in first.items()}}


def edge_checks(template):
    for count in (0, 1, 2, 3):
        case = copy.deepcopy(template)
        case["history"] = case["history"][:count]
        check_shape(case, main.forecast(case))
    case = copy.deepcopy(template)
    for row in case["history"]:
        row["rates"] = [None if i % 4 == 0 else 0 if i % 4 == 1 else 100 if i % 4 == 2 else r for i, r in enumerate(row["rates"])]
    check_shape(case, main.forecast(case))
    assert main.forecast(case)["trophies"][1]["q"][0] == 0
    # A line rising on the requested log-time scale must extrapolate upward.
    points = [(h, main.percent(-8 + 1.3 * math.log1p(h))) for h in (5, 12, 24, 36, 48, 59)]
    anchor, slope, _, distance, _ = main.robust_line(points, 168)
    assert abs(slope - 1.3) < 1e-8 and distance > 0
    assert main.percent(anchor + slope * distance) > points[-1][1]
    # Duplicating a stale observation cannot create new fitted information.
    repeated = copy.deepcopy(template)
    repeated["history"].extend(copy.deepcopy(repeated["history"][-1:]) * 20)
    assert main.forecast(repeated) == main.forecast(template)
    # Sparse input follows the specified baseline, including its rounding.
    sparse = copy.deepcopy(template)
    sparse["history"] = sparse["history"][:2]
    assert main.forecast(sparse) == baseline(sparse)
    return {"sparse_and_null_cases": "pass", "log_time_slope_recovery": "pass"}


def run():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--answers", type=Path, help="Optional JSON mapping case id to trophy-id: actual-percent")
    args = parser.parse_args()
    cases = [json.loads(p.read_text()) for p in sorted(args.input.glob("*.in"))]
    assert cases, "no practice inputs"
    report = {"practice_execution": execute_cases(args.input.resolve(), cases), "edge_checks": edge_checks(cases[0])}
    if args.answers:
        answers = json.loads(args.answers.read_text())
        pairs, house = [], []
        for case in cases:
            actual = {int(k): v for k, v in answers[case["id"]].items()}
            pairs.append((main.forecast(case), actual))
            house.append((baseline(case), actual))
        report["provided_reference_scores"] = {"model": metrics(pairs), "baseline": metrics(house)}
    else:
        report["official_practice_scores"] = "Unavailable: kit contains no reference; no submission authorized."
    report["synthetic_refresh_only"] = synthetic_refresh(cases)
    report["synthetic_growth_only"] = synthetic_growth(cases[0])
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    run()
