#!/usr/bin/env python3
"""Retrospective minimax error for nonnegative latency and memory coefficients.

Read the cases in evaluate.py's replayed evaluation. The optimistic calculation
allows native times and each feature to take independently chosen values anywhere
in their observed ranges, even if the values never occurred together. Its result
is a lower bound on the worst error obtainable by refitting this model.
"""
import argparse
from fractions import Fraction
from itertools import combinations
import json
from pathlib import Path


def minimum_worst_error(cases, optimistic):
    """Minimize the worst relative error exactly, optionally loosening each case."""
    if not cases:
        raise ValueError("expected at least one case")
    low, high = {}, {}
    for name, case in cases.items():
        def line(feature_stat, native_stat):
            native = Fraction(case[f"native_{native_stat}_ns"])
            latency, memory = (Fraction(case["features"][field][feature_stat])
                               for field in ("latency_cycles", "memory_bytes"))
            if native <= 0 or min(latency, memory) < 0:
                raise ValueError("expected positive native times and nonnegative features")
            return memory / native, (latency - memory) / native

        low[name] = line("min", "max") if optimistic else line("median", "median")
        high[name] = line("max", "min") if optimistic else low[name]

    # t is the share of the scale assigned to latency. A case's normalized
    # rate is intercept + slope*t. The upper envelope of low rates and lower
    # envelope of high rates are affine between same-envelope intersections.
    # Their sum-normalized difference is monotone or constant on each interval.
    candidates = {Fraction(0), Fraction(1)}
    for lines in (low, high):
        for (left, left_slope), (right, right_slope) in combinations(lines.values(), 2):
            if left_slope != right_slope:
                t = (right - left) / (left_slope - right_slope)
                if 0 < t < 1:
                    candidates.add(t)

    best = None
    for t in sorted(candidates):
        lows = {name: intercept + slope * t for name, (intercept, slope) in low.items()}
        highs = {name: intercept + slope * t for name, (intercept, slope) in high.items()}
        max_low, min_high = max(lows.values()), min(highs.values())
        if max_low + min_high == 0:
            # A case whose maximum rate is zero always incurs 100% error.
            scale, error = Fraction(0), Fraction(1)
        else:
            # Equalize the unavoidable overshoot and undershoot. If all rate
            # ranges overlap, this same scale places 1 in every scaled range.
            scale = 2 / (max_low + min_high)
            error = max(Fraction(0), (max_low - min_high) / (max_low + min_high))
        if best is None or error < best["error"]:
            best = {"error": error, "latency": t * scale, "memory": (1 - t) * scale,
                    "over": sorted(name for name, rate in lows.items() if error and scale * rate - 1 == error),
                    "under": sorted(name for name, rate in highs.items() if error and 1 - scale * rate == error)}

    return {"worst_error_fraction": str(best["error"]),
            "worst_error_ppm_floor": int(1_000_000 * best["error"]),
            "coefficients_ns": {"latency_cycles": str(best["latency"]), "memory_bytes": str(best["memory"])},
            "limiting_overpredictions": best["over"], "limiting_underpredictions": best["under"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evaluation", type=Path)
    args = parser.parse_args()
    cases = json.loads(args.evaluation.read_bytes(), parse_float=Fraction)["cases"]
    print(json.dumps({"analysis": "retrospective, outside preregistered scoring",
                      "cases": len(cases), "medians": minimum_worst_error(cases, False),
                      "optimistic_unpaired_ranges": minimum_worst_error(cases, True)}, indent=2))


if __name__ == "__main__":
    main()
