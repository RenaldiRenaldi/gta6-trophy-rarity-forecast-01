"""Validity, deterministic execution, and explicitly synthetic forecast diagnostics."""

import argparse
import ast
import copy
import json
import math
import os
import random
import subprocess
import sys
import tempfile
import time
import types
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


R1_COMMIT = "26dd6075580872a40bac54a1550a89ef9203f15f"
FAMILIES = ("refresh-only", "growth-only", "refresh+growth", "delayed-unlock", "high-shock")
SEEDS = {name: 601100 + i * 1000 for i, name in enumerate(FAMILIES)}
WORLD_CEILINGS = {"intro": 1.0, "story": 0.98, "optional": 0.65,
                  "completion": 0.35, "platinum": 0.08}


def load_r1():
    """Read the immutable baseline into memory, never copy a source file."""
    source = subprocess.check_output(["git", "show", R1_COMMIT + ":main.py"], text=True,
                                     cwd=Path(__file__).resolve().parent)
    module = types.ModuleType("round_one")
    exec(compile(source, R1_COMMIT + ":main.py", "exec"), module.__dict__)
    return module


def refresh_world(template, seed, real=False):
    rng = random.Random(seed)
    case = copy.deepcopy(template)
    first = case["history"][-1]
    # Count-preserving refresh; uncertainty is shared P, with small E noise.
    ratio = (638 / 3868) * math.exp(rng.gauss(0, 0.40 if real else 0.42))
    players = max(1, first["players_tracked"]) / ratio
    actual = {}
    for t, r in zip(case["trophies"], first["rates"]):
        earners = r * first["players_tracked"] / 100
        earners *= math.exp(rng.gauss(0, 0.06 if real else 0.10))
        actual[t["id"]] = 100.0 if main.kind(t) == "intro" else round(min(100, 100 * earners / players), 2)
    constrain_actual(case, actual)
    return case, actual


def constrain_actual(case, actual):
    other = [actual[t["id"]] for t in case["trophies"] if main.kind(t) != "platinum"]
    for t in case["trophies"]:
        if main.kind(t) == "platinum" and other:
            actual[t["id"]] = min(actual[t["id"]], min(other))


def count_world(template, family, seed, horizon=108.5, checkpoint=59.5, observation_noise=1.0):
    """Generate P and E first, then round 100 E/P as Exophase does.

    Historical refreshes and future innovations are sampled independently of
    either forecaster. This is a hypothetical simulator, not a game answer key.
    """
    if family == "refresh-only":
        return refresh_world(template, seed)
    rng = random.Random(seed)
    target = checkpoint + horizon
    case = copy.deepcopy(template)
    case.update(at=stamp(checkpoint), target=stamp(target), horizon_minutes=round(horizon * 60))
    initial, organic = rng.uniform(450, 850), rng.uniform(200, 600)
    jumps = []
    if family != "growth-only":
        first_time = rng.uniform(3, 6)
        first_size = (initial + organic * math.log1p(first_time)) * (math.exp(rng.gauss(math.log(3868 / 638), 0.12)) - 1)
        jumps.append((first_time, first_size))
        for center in (22, 44, 86, 130, 175):
            jumps.append((center + rng.uniform(-5, 5), first_size * rng.uniform(0.08, 0.28)))

    def players(hour):
        return initial + organic * math.log1p(hour) + sum(size for when, size in jumps if when <= hour)

    scale = players(checkpoint)
    curves = []
    for trophy in case["trophies"]:
        group = main.kind(trophy)
        cap = scale * WORLD_CEILINGS[group] * rng.uniform(0.50, 1.90)
        slope = max(0.2, rng.gauss({"intro": 1.6, "story": 1.4, "optional": 1.1,
                                   "completion": 1.3, "platinum": 1.1}[group], 0.22))
        intercept = rng.uniform(-7, -3)
        delay = rng.choice((12, 24)) if family == "delayed-unlock" else 0
        curves.append((cap, slope, intercept, delay))

    def earners(hour, curve):
        cap, slope, intercept, delay = curve
        if hour <= delay:
            return 0.0
        return cap * main.percent(intercept + slope * math.log1p(hour - delay)) / 100

    case["history"] = []
    for hour in range(1, int(checkpoint) + 1, 2):
        p = round(players(hour))
        rates = []
        for curve in curves:
            e = earners(hour, curve)
            # Printed rates carry count rounding and finite-cohort noise.
            binomial_variance = max(0.25, e * (1 - min(1, e / p)))
            e = max(0, e + rng.gauss(0, observation_noise * math.sqrt(binomial_variance)))
            rates.append(round(min(100, 100 * e / p), 2))
        row = {"at": stamp(hour), "slot": stamp(hour), "players_tracked": p, "rates": rates}
        case["history"].append(row)
        stale = copy.deepcopy(row)
        stale["at"] = stamp(hour + 0.25)
        case["history"].append(stale)
    p = players(target)
    # Unknown future denominator/earning innovations grow with elapsed days.
    shock_scale = (0.23 if family == "high-shock" else 0.11) * math.sqrt(horizon / 24)
    p *= math.exp(rng.gauss(0, shock_scale))
    actual = {}
    for trophy, curve in zip(case["trophies"], curves):
        e = earners(target, curve) * math.exp(rng.gauss(0, shock_scale))
        actual[trophy["id"]] = round(min(100, max(0, 100 * e / p)), 2)
    constrain_actual(case, actual)
    return case, actual


def paired_worlds(worlds, r1, ablations=False):
    names = ("R1", "R2", "no_shrink", "hierarchical", "point_ceiling", "asymmetric") if ablations else ("R1", "R2")
    pairs = {name: [] for name in names}
    for case, actual in worlds:
        outputs = {"R1": r1.forecast(case), "R2": main.forecast(case)}
        if ablations:
            # The selected model is already the no-pooling ablation when its
            # switch is off; reuse the immutable output rather than refit it.
            outputs.update(no_shrink=outputs["R2"] if not main.SHRINKAGE else main.forecast(case, shrink=False),
                           hierarchical=main.forecast(case, shrink=True),
                           point_ceiling=main.forecast(case, ceiling_mix=False),
                           asymmetric=main.forecast(case, asymmetric=True))
        for name, output in outputs.items():
            try:
                check_shape(case, output)
            except AssertionError as error:
                bad = [r for r in output["trophies"] if r["q"] != sorted(r["q"])]
                raise AssertionError((name, case.get("id"), bad)) from error
            pairs[name].append((output, actual))
    return {name: metrics(values) for name, values in pairs.items()}


def synthetic_suite(template, r1, trials, ablations):
    table = {}
    for family in FAMILIES:
        worlds = [count_world(template, family, SEEDS[family] + i) for i in range(trials)]
        table[family] = paired_worlds(worlds, r1, ablations)
    calibration = paired_worlds([refresh_world(template, 817210 + i, real=True) for i in range(trials)], r1)
    horizons = {}
    for minutes in (90, 1440, 2880, 6510):
        worlds = [count_world(template, "refresh+growth", 902100 + i, minutes / 60,
                              checkpoint=48 + (i % 4) * 4) for i in range(trials)]
        horizons[str(minutes)] = paired_worlds(worlds, r1)
    return {"trials_per_family": trials, "seeds": SEEDS, "families": table,
            "real_refresh_calibration_seed": 817210, "real_refresh_calibration": calibration,
            "horizon_seed": 902100, "holdout_by_horizon_minutes": horizons}


def execute_cases(input_dir, cases):
    with tempfile.TemporaryDirectory(prefix="trophy-check-") as temp:
        env = {"PATH": os.defpath, "MAKEFUN_INPUT_DIR": str(input_dir),
               "MAKEFUN_OUTPUT_DIR": temp, "PYTHONDONTWRITEBYTECODE": "1"}
        command = [sys.executable, "-P", "-s", "main.py"]
        started = time.perf_counter()
        subprocess.run(command, check=True, env=env, timeout=8, cwd=Path(__file__).resolve().parent)
        elapsed = time.perf_counter() - started
        first = {p.name: p.read_bytes() for p in Path(temp).glob("*.out")}
        assert all(p.is_file() and not p.is_symlink() for p in Path(temp).glob("*.out"))
        subprocess.run(command, check=True, env=env, timeout=8, cwd=Path(__file__).resolve().parent)
        second = {p.name: p.read_bytes() for p in Path(temp).glob("*.out")}
        assert first == second, "output differs across executions"
        assert len(first) == len(cases)
        for case in cases:
            output = json.loads(first[case["id"] + ".out"])
            check_shape(case, output)
        return {"valid_cases": len(cases), "rows_per_case": [len(c["trophies"]) for c in cases],
                "regular_output_files": True, "byte_identical": True, "clean_environment": True,
                "command": "python3 -P -s main.py", "elapsed_seconds": round(elapsed, 4),
                "output_bytes": {name: len(data) for name, data in first.items()}}


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
    xs = [math.log1p(h) for h in (5, 12, 24, 36, 48, 59)]
    _, slope, _, _ = main.line_fit(xs, [-8 + 1.3 * x for x in xs], [0.01] * len(xs))
    assert abs(slope - 1.3) < 1e-8
    # Duplicating a stale observation cannot create new fitted information.
    repeated = copy.deepcopy(template)
    repeated["history"].extend(copy.deepcopy(repeated["history"][-1:]) * 20)
    assert main.forecast(repeated) == main.forecast(template)
    # Sparse input follows the specified baseline, including its rounding.
    sparse = copy.deepcopy(template)
    sparse["history"] = sparse["history"][:2]
    assert main.forecast(sparse) == baseline(sparse)
    world, _ = count_world(template, "refresh+growth", 601234)
    deduped = main.snapshots(world)
    _, _, _, steps = main.player_path(deduped, main.hours(world["target"]))
    assert any(t > 12 for t, _, _ in steps), "late refresh was not detected"
    repeated = copy.deepcopy(world)
    repeated["history"] = [copy.deepcopy(r) for r in world["history"] for _ in range(3)]
    assert main.forecast(repeated) == main.forecast(world)
    check_shape(world, main.forecast(world))
    return {"sparse_and_null_cases": "pass", "log_time_slope_recovery": "pass",
            "late_refresh_detection": "pass", "count_history_deduplication": "pass"}


def practice_comparison(cases, r1):
    def implied(output):
        return [(main.logodds(r["q"][4]) - main.logodds(r["q"][0])) / (2 * 1.645)
                for r in output["trophies"]]
    before, after, changes = [], [], []
    for case in cases:
        a, b = r1.forecast(case), main.forecast(case)
        before.extend(implied(a))
        after.extend(implied(b))
        changes.extend(abs(x["q"][2] - y["q"][2]) for x, y in zip(a["trophies"], b["trophies"]))
    return {"R1_median_implied_sigma": main.median(before), "R2_median_implied_sigma": main.median(after),
            "median_change_mean_pp": sum(changes) / len(changes), "median_change_max_pp": max(changes)}


def audit_and_stress(template):
    root = Path(__file__).resolve().parent
    tracked = subprocess.check_output(["git", "ls-files", "*.py"], cwd=root, text=True).splitlines()
    local = {Path(name).stem for name in tracked}
    imports = {}
    for name in tracked:
        modules = set()
        for node in ast.walk(ast.parse((root / name).read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                modules.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                modules.add((node.module or "").split(".")[0])
        assert modules <= set(sys.stdlib_module_names) | local, (name, modules)
        imports[name] = sorted(modules)
    world, _ = count_world(template, "refresh+growth", 603100)
    expanded = []
    for hour, row in main.snapshots(world):
        for j in range(10):
            expanded.append(dict(row, at=stamp(hour + j / 20),
                                 rates=[round(min(100, max(0, r + (j % 3 - 1) * 0.01)), 2)
                                        for r in row["rates"]]))
    world["history"] = expanded
    started = time.perf_counter()
    output = main.forecast(world)
    elapsed = time.perf_counter() - started
    check_shape(world, output)
    assert elapsed < 8, "dense trend forecast exceeded the server limit"
    owner = json.loads((root / ".makefun/owner.json").read_text())
    assert owner["wallet"] == "9XAmKVSK2jKwExuis5E2WyrgsKx5F99gfuUrvbqXv52p"
    return {"imports": imports, "wallet_claim": "pass", "dense_unique_snapshots": len(main.snapshots(world)),
            "dense_trend_seconds": round(elapsed, 4), "python_version": sys.version.split()[0]}


def release_checks(report):
    suite, spread = report["synthetic_count_space"], report["practice_spread"]
    families = suite["families"]
    calibration = suite["real_refresh_calibration"]
    horizons = list(suite["holdout_by_horizon_minutes"].values())
    checks = {
        "at_least_60_trials": suite["trials_per_family"] >= 60,
        "R2_loss_below_R1_every_family": all(r["R2"]["loss"] < r["R1"]["loss"] for r in families.values()),
        "combined_growth_median_improves": families["refresh+growth"]["R2"]["typical_miss_pp"] <= families["refresh+growth"]["R1"]["typical_miss_pp"],
        "real_refresh_coverage_90_to_95": 0.90 <= calibration["R2"]["coverage_90"] <= 0.95,
        "real_refresh_loss_improves": calibration["R2"]["loss"] < calibration["R1"]["loss"],
        "practice_sigma_040_to_045": 0.40 <= spread["R2_median_implied_sigma"] <= 0.45,
        "practice_medians_preserved": spread["median_change_max_pp"] < 0.01,
        "horizon_coverage_near_90": all(0.87 <= r["R2"]["coverage_90"] <= 0.95 for r in horizons),
        "horizon_loss_increases_gradually": all(a["R2"]["loss"] < b["R2"]["loss"] for a, b in zip(horizons, horizons[1:])),
        "unvalidated_asymmetry_disabled": not main.ASYMMETRIC,
    }
    if "no_shrink" in families["growth-only"]:
        growth = families["growth-only"]
        hierarchy_accepted = (growth["hierarchical"]["loss"] < growth["no_shrink"]["loss"]
                              and all(r["hierarchical"]["loss"] <= r["no_shrink"]["loss"] for r in families.values()))
        checks["hierarchy_obeys_no_regression_ship_rule"] = hierarchy_accepted or not main.SHRINKAGE
        checks["ceiling_mixture_wins_target_growth_ablation"] = growth["R2"]["loss"] < growth["point_ceiling"]["loss"]
    return checks


def run():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--answers", type=Path, help="Optional JSON mapping case id to trophy-id: actual-percent")
    parser.add_argument("--trials", type=int, default=60, help="At least 60 for release validation")
    parser.add_argument("--ablations", action="store_true", help="Evaluate shrinkage, ceilings, and asymmetry on identical worlds")
    args = parser.parse_args()
    cases = [json.loads(p.read_text()) for p in sorted(args.input.glob("*.in"))]
    assert cases, "no practice inputs"
    r1 = load_r1()
    report = {"practice_execution_and_server_sim": execute_cases(args.input.resolve(), cases),
              "edge_checks": edge_checks(cases[0]), "practice_spread": practice_comparison(cases, r1),
              "engineering_audit": audit_and_stress(cases[0])}
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
    report["synthetic_count_space"] = synthetic_suite(cases[0], r1, args.trials, args.ablations)
    report["release_checks"] = release_checks(report)
    report["selected_components"] = {"hierarchical_shrinkage": main.SHRINKAGE,
                                      "explicit_asymmetric_spread": main.ASYMMETRIC,
                                      "reason": "Candidates remain available for ablation; regressions prevent default enablement."}
    report["source_state"] = {
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], text=True).strip()),
    }
    print(json.dumps(report, indent=2))
    if args.trials >= 60 and not all(report["release_checks"].values()):
        raise SystemExit("release checks failed; inspect the labeled report")


if __name__ == "__main__":
    run()
