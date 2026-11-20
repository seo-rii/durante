#!/usr/bin/env python3
"""Certify calibration-only minimax errors for nonnegative linear CPP20 timing models."""
import argparse
from fractions import Fraction
from hashlib import sha256
import json
from math import ceil, floor
from pathlib import Path

from branch_load_interaction import require
from memory_cost_model import LABELS, LABELS_SHA256, MODELS, training_rows


def normalized_rows(observations):
    # Minimize e with x.w/U - e <= 1 and -x.w/L - e <= -1.
    # Each repeat may choose its most favorable native target in [L,U].
    upper = [[Fraction(x, high) for x in counts] for counts, _, high in observations]
    lower = [[Fraction(x, low) for x in counts] for counts, low, _ in observations]
    return upper, lower


def certificate(upper, lower, weights, dual):
    # HiGHS multipliers on upper then lower inequalities. Round to integer
    # units and repair any deficits by adding mass to all upper inequalities.
    units = 10**15
    mass = [max(0, round(v * units)) for v in dual]
    n = len(upper)
    correction = 0
    for up, lo in zip(zip(*upper), zip(*lower)):
        total = sum(up)
        if total:
            deficit = sum(a * x for a, x in zip(mass[n:], lo)) - sum(b * y for b, y in zip(mass[:n], up))
            correction = max(correction, ceil(deficit / total))
    return {
        "primal_ns_per_unit": [str(max(0.0, float(w))) for w in weights],
        "dual_nonzero_units": [[i, value] for i, value in enumerate(mass) if value],
        "extra_units_per_upper_row": correction,
        "dual_denominator": max(units, sum(mass) + n * correction),
    }


def verify(upper, lower, proof):
    """Bound the optimum with exact rational arithmetic, independently of the LP solver."""
    weights = [Fraction(w) for w in proof["primal_ns_per_unit"]]
    require(len(weights) == len(upper[0]) and min(weights) >= 0, "invalid primal weights")
    n = len(upper)
    correction = proof["extra_units_per_upper_row"]
    require(type(correction) is int and correction >= 0, "invalid dual correction")
    mass = [correction] * n + [0] * n
    for i, units in proof["dual_nonzero_units"]:
        require(type(i) is int and 0 <= i < 2 * n and type(units) is int and units > 0, "invalid dual term")
        mass[i] += units
    denominator = proof["dual_denominator"]
    require(type(denominator) is int and denominator > 0 and sum(mass) <= denominator,
            "invalid dual normalization")
    # Nonnegative coefficients make each lower weighted feature sum bounded by
    # the corresponding upper sum. Combined with sum(mass)/denominator <= 1,
    # this gives e >= (sum(lower mass) - sum(upper mass))/denominator.
    for up, lo in zip(zip(*upper), zip(*lower)):
        up_sum = sum(a * x for a, x in zip(mass[:n], up))
        lo_sum = sum(a * x for a, x in zip(mass[n:], lo))
        require(up_sum >= lo_sum, "invalid dual feature constraint")
    dual_bound = max(Fraction(0), Fraction(sum(mass[n:]) - sum(mass[:n]), denominator))
    primal_bound = Fraction(0)
    for up, lo in zip(upper, lower):
        overshoot = sum(x * w for x, w in zip(up, weights)) - 1
        undershoot = 1 - sum(x * w for x, w in zip(lo, weights))
        primal_bound = max(primal_bound, overshoot, undershoot)
    require(dual_bound <= primal_bound, "invalid certificate bounds")
    return dual_bound, primal_bound


def minimize(observations):
    import numpy as np
    from scipy.optimize import linprog

    upper, lower = normalized_rows(observations)
    constraints = np.vstack((np.asarray(upper, dtype=float), -np.asarray(lower, dtype=float)))
    scale = np.max(np.abs(constraints), axis=0)
    # Unobserved or collinear counters are legal in a capacity calculation.
    scale[scale == 0] = 1
    design = np.column_stack([constraints / scale, -np.ones(len(constraints))])
    # Minimize signed violations, then clip the worst violation to zero. An
    # interior solution can witness exact feasibility despite weight rounding.
    outcome = linprog([0] * len(scale) + [1], A_ub=design, b_ub=[1] * len(upper) + [-1] * len(lower),
                      bounds=[(0, None)] * len(scale) + [(None, None)], method="highs")
    require(outcome.success, f"minimax solver failed: {outcome.message}")
    proof = certificate(upper, lower, outcome.x[:-1] / scale, -outcome.ineqlin.marginals)
    low, high = verify(upper, lower, proof)
    require(high - low <= Fraction(1, 10**9), "certificate gap exceeds one part per billion")
    return {"lower_error_ppb": floor(low * 10**9), "upper_error_ppb": ceil(high * 10**9),
            "certificate": proof}


def analyze(features, labels):
    training = training_rows(features, labels)
    results = {}
    for model, fields in MODELS.items():
        results[model] = {"fields": list(fields)}
        for target in ("median", "favorable_range"):
            observations = []
            for case in training:
                native = sorted(labels["training"][case["id"]]["native_ns"])
                low = high = Fraction(native[2] + native[3], 2)
                if target == "favorable_range":
                    low, high = native[0], native[-1]
                for row in case["rows"]:
                    observations.append(([row[k] for k in fields], low, high))
            results[model][target] = minimize(observations)
    return {"schema": 1, "case_order": [case["id"] for case in training],
            "feature_repetitions": 3, "native_samples_per_case": 6, "models": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("features", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    features, original = args.features.read_bytes(), LABELS.read_bytes()
    require(sha256(original).hexdigest() == LABELS_SHA256, "unexpected original label file")
    result = analyze(json.loads(features), json.loads(original))
    result.update(features_sha256=sha256(features).hexdigest(), labels_sha256=LABELS_SHA256)
    with args.output.open("x") as out:
        json.dump(result, out, indent=2, allow_nan=False)
        out.write("\n")


if __name__ == "__main__":
    main()
