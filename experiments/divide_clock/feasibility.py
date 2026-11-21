#!/usr/bin/env python3
"""Retrospective feasibility of the registered divider grid on assembly controls and Rust GCD."""
import argparse
from fractions import Fraction
import json
from statistics import median

from score import HISTORICAL, PLAN, coefficients, historical, predictions, require, sha


def nearest_error(pred_lo, pred_hi, native_lo, native_hi):
    """Smallest absolute error attainable with independent choices over two continuous ranges."""
    if pred_lo > native_hi:
        return pred_lo/native_hi - 1
    if pred_hi < native_lo:
        return pred_hi/native_lo - 1
    return Fraction(0)


def scan(cases, plan):
    weights = coefficients(plan)
    names = plan["train"] + plan["assembly_validation"] + ["gcd"]
    rust = plan["rust_validation"]
    tolerance = Fraction(plan["tolerance_ppm"], 1_000_000)
    rows = []
    rust_ranges = {name: [] for name in rust}
    low_l, high_l = plan["latency_cycles"]
    low_i, high_i = plan["interval_cycles"]
    for latency in range(low_l, high_l+1):
        for interval in range(low_i, min(high_i, latency)+1):
            losses = {"median": {}, "favorable_ranges": {}}
            for name in cases:
                case = cases[name]
                values = predictions(case, (latency,interval,True), weights)
                worst = max((p / case["target"] - 1 for p in values), key=abs)
                favorable = nearest_error(min(values), max(values), min(case["native"]), max(case["native"]))
                if name in names:
                    losses["median"][name] = worst
                    losses["favorable_ranges"][name] = favorable
                if name in rust_ranges:
                    rust_ranges[name].append((median(values), abs(worst), abs(favorable)))
            rows.append((latency, interval, losses))
    result = {}
    for criterion in ("median", "favorable_ranges"):
        def worst(row):
            return max(map(abs, row[2][criterion].values()))
        best = min(rows, key=lambda row: (worst(row), row[1], abs(row[0]-20), row[0]))
        result[criterion] = {
            "passing_parameters": [[l,i] for l,i,errors in rows if max(map(abs, errors[criterion].values())) <= tolerance],
            "minimax_parameters": list(best[:2]),
            "minimax_worst_error_ppm": round(1_000_000*worst(best)),
            "minimax_limiting_cases": [n for n,e in best[2][criterion].items() if abs(e) == worst(best)],
            "minimax_signed_errors_ppm": {n: round(e*1_000_000) for n,e in best[2][criterion].items()},
        }
    bounds = {}
    for name, trials in rust_ranges.items():
        estimates, strict, favorable = zip(*trials)
        bounds[name] = {"median_prediction_min_ns": round(min(estimates)), "median_prediction_max_ns": round(max(estimates)),
                        "prediction_unchanged_over_grid": min(estimates)==max(estimates),
                        "best_error_to_median_ppm": round(min(strict)*1_000_000),
                        "best_error_with_favorable_ranges_ppm": round(min(favorable)*1_000_000),
                        "median_feasible": min(strict) <= tolerance, "favorable_ranges_feasible": min(favorable) <= tolerance}
    return {"schema": 1, "protocol_sha256": sha(PLAN.read_bytes()), "grid_size": len(rows),
            "parameter_order": ["latency_cycles","interval_cycles"], "workloads": names, **result, "rust": bounds}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    result = scan(historical(), json.loads(PLAN.read_bytes()))
    out = HISTORICAL/"feasibility.json"
    if args.verify:
        require(json.loads(out.read_bytes()) == result, "different retrospective grid result")
    else:
        out.write_text(json.dumps(result, indent=2)+"\n")
    print("Scanned", result["grid_size"], "latency and interval pairs.",
          "Feasible at medians:", result["median"]["passing_parameters"],
          "Feasible with favorable ranges:", result["favorable_ranges"]["passing_parameters"])
