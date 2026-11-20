#!/usr/bin/env python3
"""Bound six-counter predictions under the crossed study's calibration tolerances."""
import argparse
from fractions import Fraction
import json
from pathlib import Path

from branch_load_interaction import require
from crossed_memory_model import (CANDIDATE, LABELS, LABELS_SHA256, ORIGINAL, ORIGINAL_FEATURES_SHA256,
                                  new_cases, read_pinned, samples, training_rows)
from memory_cost_capacity import minimize


def endpoint(objective, sign, ratios, scale, matrix, bounds, tolerance):
    import numpy as np
    from scipy.optimize import linprog

    # Scale columns and the objective independently; the reported witness
    # uses the original counters and nanoseconds per count.
    scaled = np.asarray(objective, dtype=float) / scale
    units = np.max(scaled)
    require(units > 0, "empty prediction objective")
    cost = sign * scaled / units
    solution = linprog(cost, A_ub=matrix, b_ub=bounds, bounds=(0, None), method="highs")
    require(solution.success, f"prediction bound failed: {solution.message}")
    weights = solution.x / scale
    worst = float(np.max(np.abs(ratios @ weights - 1)))
    multipliers = -solution.ineqlin.marginals
    dual_slack = cost + matrix.T @ multipliers
    gap = abs(solution.fun + bounds @ multipliers)
    require(min(weights) >= 0 and worst <= tolerance + 1e-8 and min(multipliers) >= -1e-9
            and min(dual_slack) >= -1e-8 and gap <= 1e-8 * max(1, abs(solution.fun)),
            "uncertified prediction bound")
    prediction = float(np.dot(objective, weights))
    require(abs(prediction - sign * units * solution.fun) <= 1e-8 * max(1, prediction),
            "objective does not match the witness")
    return {"prediction_ns": prediction, "ns_per_unit": list(weights),
            "worst_training_relative_error": worst, "scaled_duality_gap": float(gap)}


def study(training, targets):
    import numpy as np
    from scipy.optimize import linprog

    observations = []
    for case in training:
        center = Fraction(case["native"])
        for row in case["rows"]:
            observations.append(([row[k] for k in CANDIDATE], center, center))
    minimax = minimize(observations)
    ratios = np.array([[float(Fraction(x) / center) for x in counts] for counts, center, _ in observations])
    scale = ratios.max(axis=0)
    require(all(scale > 0), "unmeasured counter could have an unbounded coefficient")
    design = ratios / scale
    matrix = np.vstack((design, -design))
    result = {"training_cases": len(training), "training_replays": len(observations), "minimax": minimax,
              "tolerances": {}}
    for percent in (6, 20):
        tol = percent / 100
        bounds = np.repeat([1 + tol, tol - 1], len(design))
        feasible = linprog(np.zeros(len(CANDIDATE)), A_ub=matrix, b_ub=bounds,
                           bounds=(0, None), method="highs")
        if feasible.status == 2:
            require(minimax["lower_error_ppb"] > percent * 10_000_000, "infeasibility not supported by minimax")
            result["tolerances"][str(percent)] = {"feasible": False}
            continue
        require(feasible.success, f"feasibility check failed: {feasible.message}")
        predictions = {}
        for name, rows in sorted(targets.items()):
            # Distinct counter repetitions may have distinct predictions.
            # Report the envelope of all repeats and retain its two witnesses.
            distinct = {tuple(r[k] for k in CANDIDATE): i for i, r in enumerate(rows, 1)}
            extremes = {}
            for side, sign in (("min", 1), ("max", -1)):
                candidates = []
                for counts, repetition in distinct.items():
                    witness = endpoint(counts, sign, ratios, scale, matrix, bounds, tol)
                    candidates.append({**witness, "repetition": repetition})
                extremes[side] = min(candidates, key=lambda w: sign * w["prediction_ns"])
            predictions[name] = {**extremes, "distinct_work_vectors": len(distinct)}
        result["tolerances"][str(percent)] = {"feasible": True, "cases": predictions}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ("features", "calibration", "manifest"):
        parser.add_argument(arg, type=Path)
    parser.add_argument("--features-sha256", required=True)
    parser.add_argument("--calibration-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    historical = read_pinned(ORIGINAL / "features.json", ORIGINAL_FEATURES_SHA256)
    labels = read_pinned(LABELS, LABELS_SHA256)
    features = read_pinned(args.features, args.features_sha256)
    calibration = read_pinned(args.calibration, args.calibration_sha256)
    manifest_sha = calibration["manifest_sha256"]
    manifest = read_pinned(args.manifest, manifest_sha)
    require(features["provenance"]["manifest_sha256"] == manifest_sha, "feature manifest mismatch")
    original = training_rows(historical, labels)
    added, external = new_cases(features, calibration, manifest, historical)
    targets = {name: rows for name, (_, rows) in external.items()}
    targets["fenwick-prefix"] = samples(historical["external"]["fenwick-prefix"])
    result = {"schema": 1, "fields": list(CANDIDATE), "features_sha256": args.features_sha256,
              "calibration_sha256": args.calibration_sha256, "manifest_sha256": manifest_sha,
              "original_features_sha256": ORIGINAL_FEATURES_SHA256, "original_labels_sha256": LABELS_SHA256,
              "original": study(original, targets), "expanded": study(original + added, targets)}
    with args.output.open("x") as out:
        json.dump(result, out, indent=2, allow_nan=False)
        out.write("\n")


if __name__ == "__main__":
    main()
