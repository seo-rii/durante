#!/usr/bin/env python3
"""Fit the predeclared models on calibration cases and score the held-out cases.

  python3 -B experiments/capacity_misses/analyze.py RESULTS.json [--output SCORES.json]

Each model is a nonnegative, zero-intercept weighted sum of counters fitted on
the calibration medians by minimizing the sum of squared relative errors. The
optimum is found exactly by solving every active set. Held-out scores use the
frozen weights. See predeclared.txt.
"""
import argparse
import itertools
import json
from pathlib import Path
import statistics

import numpy as np

MODELS = {
    "M0": ["instructions", "rep_bytes"],
    "M1": ["instructions", "rep_bytes", "l1d_misses"],
    "M2": ["instructions", "rep_bytes", "l1d_misses", "random_2m", "random_32m", "stream_2m"],
    "M3": ["instructions", "rep_bytes", "random_256k", "random_2m", "random_32m"],
}
LIMITS = (0.8, 1.25)
STABLE_SPREAD = 0.10


def features(probe):
    misses, stream = probe["outer_misses"], probe["outer_stream"]
    return {
        "instructions": probe["instructions"],
        "rep_bytes": probe["movs_bytes"] + probe["stos_bytes"],
        "l1d_misses": probe["l1d_misses"],
        "random_256k": misses[0] - stream[0],
        "random_2m": misses[1] - stream[1],
        "random_32m": misses[2] - stream[2],
        "stream_2m": stream[1],
    }


def fit(rows, names):
    """rows: list of (feature dict, seconds). Returns weights (seconds per unit)."""
    X = np.array([[f[n] for n in names] for f, _ in rows], dtype=float)
    y = np.array([t for _, t in rows], dtype=float)
    scale = np.where(X.max(axis=0) > 0, X.max(axis=0), 1.0)
    A = (X / scale) / y[:, None]
    b = np.ones(len(rows))
    best = None
    for size in range(1, len(names) + 1):
        for support in itertools.combinations(range(len(names)), size):
            cols = list(support)
            sol, *_ = np.linalg.lstsq(A[:, cols], b, rcond=None)
            if np.any(sol < 0):
                continue
            w = np.zeros(len(names))
            w[cols] = sol
            loss = float(np.sum((A @ w - b) ** 2))
            if best is None or loss < best[0] - 1e-15:
                best = (loss, w)
    loss, w = best
    return {n: float(w[i] / scale[i]) for i, n in enumerate(names)}, loss


def predict(weights, f):
    return sum(weights[n] * f[n] for n in weights)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("results", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    data = json.loads(args.results.read_text())
    cases = {}
    for name, case in data["cases"].items():
        times = case["native_cpu_s"]
        median = statistics.median(times)
        cases[name] = {"role": case["role"], "native_median_s": median,
                       "native_spread": (max(times) - min(times)) / median,
                       "stable": (max(times) - min(times)) / median <= STABLE_SPREAD,
                       "probe_repeatable": case["probe_repeatable"],
                       "features": features(case["probe"][0])}
    calibration = [(c["features"], c["native_median_s"]) for c in cases.values() if c["role"] == "calibration"]
    report = {"models": {}, "cases": cases}
    for model, names in MODELS.items():
        weights, loss = fit(calibration, names)
        rows, verdicts = [], {"agree": 0, "false_tle": 0, "false_ac": 0}
        for name, c in cases.items():
            if c["role"] != "heldout":
                continue
            p = predict(weights, c["features"])
            err = (p - c["native_median_s"]) / c["native_median_s"]
            rows.append({"case": name, "stable": c["stable"], "prediction_s": p, "error": err})
            for factor in LIMITS:
                limit = factor * c["native_median_s"]
                native_tle = c["native_median_s"] > limit
                predicted_tle = p > limit
                if native_tle == predicted_tle:
                    verdicts["agree"] += 1
                elif predicted_tle:
                    verdicts["false_tle"] += 1
                else:
                    verdicts["false_ac"] += 1

        def summary(subset):
            errors = [abs(r["error"]) for r in subset]
            return {"cases": len(subset), "within_20pct": sum(e <= 0.20 for e in errors),
                    "median_abs_error": statistics.median(errors) if errors else None,
                    "worst_abs_error": max(errors) if errors else None}

        calib_errors = [abs(predict(weights, f) - t) / t for f, t in calibration]
        report["models"][model] = {
            "features": names, "weights_s_per_unit": weights, "calibration_loss": loss,
            "calibration_worst_abs_error": max(calib_errors),
            "heldout_all": summary(rows), "heldout_stable": summary([r for r in rows if r["stable"]]),
            "verdicts": verdicts, "rows": rows}
    m = report["models"]
    stable = {k: v["heldout_stable"] for k, v in m.items()}
    report["decision"] = {
        "rule": "M2 improves only if, on stable held-out cases, it has more cases within 20% than M0 and M1 "
                "and a worst absolute error no larger than M1's",
        "m2_improves": stable["M2"]["within_20pct"] > max(stable["M0"]["within_20pct"], stable["M1"]["within_20pct"])
                       and stable["M2"]["worst_abs_error"] <= stable["M1"]["worst_abs_error"]}
    print("model  calib_worst  within20(all)  within20(stable)  median|err|  worst|err|  verdict agree/FT/FA")
    for k, v in m.items():
        a, s, vd = v["heldout_all"], v["heldout_stable"], v["verdicts"]
        print(f"{k:5} {v['calibration_worst_abs_error']:10.1%}   {a['within_20pct']:3}/{a['cases']:<3}        "
              f"{s['within_20pct']:3}/{s['cases']:<3}        {a['median_abs_error']:7.1%}   {a['worst_abs_error']:8.1%}   "
              f"{vd['agree']}/{vd['false_tle']}/{vd['false_ac']}")
    print("decision:", report["decision"]["m2_improves"])
    if args.output:
        args.output.write_text(json.dumps(report, indent=1) + "\n")


if __name__ == "__main__":
    main()
