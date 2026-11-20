#!/usr/bin/env python3
"""Score frozen CPP20 predictions against the native CPU readings and executed time limits."""
import argparse
from fractions import Fraction
import hashlib
import json
from math import isfinite
from pathlib import Path
from statistics import median

from branch_load_interaction import identity, require
from memory_cost_model import CANDIDATE, EXTERNAL, MODELS

AC = "Accepted"
TLE = "Time Limit Exceeded"
NS_PER_MS = 1_000_000


def limits(center_ns):
    return {name: max(1, int(center_ns * Fraction(n, 5 * NS_PER_MS) + Fraction(1, 2)))
            for name, n in (("low", 4), ("high", 6))}


def agreement(status, native):
    hits = native.count(status)
    if hits == len(native):
        return "agreement"
    return "opposition" if hits == 0 else "mixed"


def score_case(name, frozen, observed):
    require(identity(frozen) == identity(observed), f"{name}: artifact, input or oracle changed")
    runs = observed["rounds"]
    require(sorted(r["repetition"] for r in runs) == [1, 2, 3], f"{name}: incomplete full-run triplets")
    native = sorted(r[key] for r in runs for key in ("native_before_ns", "native_after_ns"))
    require(all(type(v) is int and v > 0 for v in native), f"{name}: invalid native CPU reading")
    for run in runs:
        for phase in ("before", "after"):
            sample = run["native_" + phase]
            require(sample["status"] == AC and sample["cpu_ns"] == run[f"native_{phase}_ns"],
                    f"{name}: native bracket contradicts its receipt")
    center = Fraction(native[2] + native[3], 2)
    spread = (max(native) - min(native)) / center
    models = {}
    for model in MODELS:
        estimates = frozen["predictions_ns"][model]
        require(len(estimates) == 3 and all(type(v) is int and v >= 0 for v in estimates),
                f"{name}: expected three integer predictions")
        point = median(estimates)
        require(point == frozen[model + "_median_ns"], f"{name}: prediction median mismatch")
        models[model] = {"median_ns": point, "error_percent": float(100 * (point - center) / center),
                         "within_20_percent": all(5 * abs(v - center) <= center for v in estimates)}

    bounds = {label + "_" + side: ms for label, ns in (("native", center), ("candidate", models["candidate"]["median_ns"]))
              for side, ms in limits(ns).items()}
    rows = observed["boundaries"]
    require(all(type(row["limit_ms"]) is int and row["limit_ms"] > 0 for row in rows),
            f"{name}: invalid time limit")
    require(sorted(row["limit_ms"] for row in rows) == sorted(set(bounds.values())),
            f"{name}: missing, duplicated or unexpected boundary")
    results = []
    for row in sorted(rows, key=lambda r: r["limit_ms"]):
        ms, actual = row["limit_ms"], row["native"]
        require(isinstance(actual, list) and len(actual) == 3 and all(s in (AC, TLE) for s in actual),
                f"{name}: incomplete native boundary verdicts")
        require(actual == [sample["status"] for sample in row["native_samples"]],
                f"{name}: boundary verdict contradicts its native receipts")
        labels = [label for label, value in bounds.items() if value == ms]
        require(set(row["roles"]) == set(labels), f"{name}: contradictory boundary role")
        comparisons = {}
        for model in MODELS:
            predicted = TLE if models[model]["median_ns"] > ms * NS_PER_MS else AC
            comparisons[model] = {"verdict": predicted, "native_matches": actual.count(predicted),
                                  "comparison": agreement(predicted, actual)}
        live = row.get("v8")
        sample = row.get("v8_sample")
        require(live == (sample["status"] if sample is not None else None),
                f"{name}: boundary verdict contradicts its live receipt")
        require(row["candidate"] == comparisons["candidate"]["verdict"],
                f"{name}: contradictory recorded candidate verdict")
        if ms in (bounds["native_low"], bounds["native_high"]):
            require(live in (AC, TLE), f"{name}: missing historical live verdict")
            comparisons["historical_live"] = {"verdict": live, "native_matches": actual.count(live),
                                              "comparison": agreement(live, actual)}
        else:
            require(live is None, f"{name}: unexpected historical live verdict")
        results.append({"limit_ms": ms, "labels": labels, "native": actual, "comparisons": comparisons})

    stability = frozen["counter_spread"]
    require(set(stability) == set(CANDIDATE) and all(v is None or
            (type(v) in (int, float) and isfinite(v) and v >= 0) for v in stability.values()),
            f"{name}: invalid diagnostic counter spread")
    return {"native_median_ns": float(center), "native_spread_percent": float(spread * 100),
            "duration_pass": min(native) >= 20_000_000, "native_spread_pass": spread <= Fraction(1, 10),
            "counter_spread_pass": all(v is not None and v <= .001 for v in stability.values()),
            "models": models, "boundaries": results}


def score(prediction_bytes, cloud):
    predictions = json.loads(prediction_bytes)
    require(predictions["schema"] == cloud["schema"] == 1, "unsupported input schema")
    require(hashlib.sha256(prediction_bytes).hexdigest() == cloud["predictions_sha256"],
            "Cloud Run evidence does not match the sealed predictions")
    require(set(predictions["external"]) == set(cloud["cases"]) == EXTERNAL, "wrong prospective case set")
    cases = {name: score_case(name, predictions["external"][name], cloud["cases"][name]) for name in sorted(EXTERNAL)}
    summary = {}
    for model in MODELS:
        accurate = sum(c["models"][model]["within_20_percent"] for c in cases.values())
        stable = all(c["duration_pass"] and c["native_spread_pass"] and c["counter_spread_pass"] for c in cases.values())
        summary[model] = {"accurate_programs": accurate, "programs": len(cases),
                          "passes_duration_and_stability": stable, "passes_all_gates": stable and accurate == len(cases)}
    return {"cases": cases, "summary": summary}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("predictions", type=Path)
    parser.add_argument("cloud", type=Path)
    args = parser.parse_args()
    try:
        scored = score(args.predictions.read_bytes(), json.loads(args.cloud.read_bytes()))
    except (KeyError, TypeError, ValueError) as error:
        parser.error(str(error))
    print(json.dumps(scored, indent=2, allow_nan=False))
