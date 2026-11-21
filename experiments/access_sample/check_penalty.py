#!/usr/bin/env python3
"""Post-hoc bounds on one nonnegative penalty for scattered serial loads and Rust lower_bound.

Run from the repository root:
    python3 experiments/access_sample/check_penalty.py experiments/results/gce-access-sample/dataset.json.gz
"""
import argparse
from fractions import Fraction
from pathlib import Path

from score import load_features
from study import ROOT, load_plan, read_json, require, sha, validated_rows


def intervals(native, samples, feature, tolerance):
    # Allow each workload its most favorable native time and diagnostic. Even
    # gaps within the union of feasible diagnostic intervals are accepted.
    floor = (1 - tolerance) * min(native)
    ceiling = (1 + tolerance) * max(native)
    offsets = [s["baseline"] for s in samples]
    signals = [s[feature] for s in samples]
    require(min(signals) > 0 and max(offsets) < floor, "expected strictly positive penalties for these cases")
    paired = [((floor-b)/x, (ceiling-b)/x) for b, x in zip(offsets, signals, strict=True)]
    envelope = (min(lo for lo, _ in paired), max(hi for _, hi in paired))
    # Relax a second constraint: mix independently favorable baseline and
    # feature endpoints even if they never occurred on the same measured run.
    relaxed = ((floor-max(offsets))/max(signals), (ceiling-min(offsets))/min(signals))
    require(relaxed[0] <= envelope[0] <= envelope[1] <= relaxed[1], "invalid outer bound")
    return envelope, relaxed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    data = read_json(parser.parse_args().dataset)
    plan = load_plan(data)
    reference = ROOT / plan["clock"]["evaluation"]
    require(sha(reference.read_bytes()) == plan["clock"]["sha256"], "changed calibration")
    weights = read_json(reference)["models_on_medians"]["latency_memory"]["coefficients_ns"]
    require(weights == plan["clock"]["coefficients_ns"], "changed baseline")
    weights = {name: Fraction(value) for name, value in weights.items()}
    tolerance = Fraction(plan["thresholds"]["max_error_ppm"], 1_000_000)
    require(tolerance == Fraction(1, 5), "expected the registered 20% tolerance")
    scatter, search = "load_scattered_serial", "lower_bound"
    cases = {name: ([], []) for name in (scatter, search)}
    for _, row in validated_rows(data, plan):
        if row["workload_id"] in cases:
            native, samples = cases[row["workload_id"]]
            native.extend(row[phase]["cpu_time_ns"] for phase in ("native_before", "native_after"))
            features = load_features(row["sampled"], weights)
            require(features["far"] == features["scaled_far"], "penalty features differ for this workload")
            samples.append(features)

    for feature in ("far", "scaled_far"):
        outer = {}
        for name, (native, samples) in cases.items():
            require(len(native) == 6 and len(samples) == 3, "incomplete workload")
            envelope, outer[name] = intervals(native, samples, feature, tolerance)
            display = " ".join(f"[{float(lo):.9f}, {float(hi):.9f}]" for lo, hi in (envelope, outer[name]))
            print(f"{name} {feature}: native [{min(native)}, {max(native)}] ns; "
                  f"paired and independently relaxed coefficient intervals (ns/unit) {display}")
        gap = outer[scatter][0] - outer[search][1]
        require(gap > 0, "the two workloads have overlapping outer penalty intervals")
        print(f"{feature}: disjoint outer bounds, gap {float(gap):.9f} ns/unit")


if __name__ == "__main__":
    main()
