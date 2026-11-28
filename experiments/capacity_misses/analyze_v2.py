#!/usr/bin/env python3
"""Score the v2 capacity-miss study and its verdict-disagreement analysis.

  python3 -B experiments/capacity_misses/analyze_v2.py RESULTS.json [--output SCORES.json]

Implements predeclared-v2.txt: frozen nonnegative fits on the calibration cases,
accuracy on held-out contest variants, and native-versus-model verdicts under an
absolute and a relative time limit.
"""
import argparse
import json
from pathlib import Path
import statistics

from analyze import fit, predict

MODELS = {
    "M0": ["instructions", "rep_bytes"],
    "ML": ["latency_x16", "rep_bytes"],
    "M1": ["instructions", "rep_bytes", "l1d_misses"],
    "M2": ["instructions", "rep_bytes", "l1d_misses", "random_2m", "random_32m", "stream_2m"],
    "M4": ["instructions", "rep_bytes", "l1d_misses", "random_2m", "random_32m", "stream_2m",
           "l1d_dependent", "dependent_2m"],
    "ML4": ["latency_x16", "rep_bytes", "l1d_misses", "random_2m", "random_32m", "stream_2m",
            "l1d_dependent", "dependent_2m"],
}
STABLE_SPREAD = 0.20
FACTORS = (1.5, 2.0, 3.0)


def features(probe):
    misses, stream, dependent = probe["outer_misses"], probe["outer_stream"], probe["outer_dependent"]
    return {
        "instructions": probe["instructions"], "rep_bytes": probe["movs_bytes"] + probe["stos_bytes"],
        "latency_x16": probe["latency_bound_x16"], "l1d_misses": probe["l1d_misses"],
        "random_2m": misses[1] - stream[1], "random_32m": misses[2] - stream[2], "stream_2m": stream[1],
        "l1d_dependent": probe["l1d_dependent"], "dependent_2m": dependent[1],
    }


def summary(rows):
    errors = [abs(r["error"]) for r in rows]
    return {"cases": len(rows), "within_20pct": sum(e <= 0.20 for e in errors),
            "median_abs_error": statistics.median(errors) if errors else None,
            "worst_abs_error": max(errors) if errors else None}


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
        cases[name] = {"role": case["role"], "meta": case.get("meta") or {}, "native_median_s": median,
                       "native_min_s": min(times), "native_max_s": max(times),
                       "native_spread": (max(times) - min(times)) / median,
                       "stable": (max(times) - min(times)) / median <= STABLE_SPREAD,
                       "features": features(case["probe"][0])}
    calibration = {n: c for n, c in cases.items() if c["role"] == "calibration"}
    heldout = {n: c for n, c in cases.items() if c["role"] == "heldout"}
    problems = sorted({c["meta"]["problem"] for c in heldout.values()})
    report = {"models": {}, "cases": cases}
    for model, names in MODELS.items():
        weights, loss = fit([(c["features"], c["native_median_s"]) for c in calibration.values()], names)
        rows = []
        for name, c in heldout.items():
            p = predict(weights, c["features"])
            rows.append({"case": name, "problem": c["meta"]["problem"], "too_slow": c["meta"]["too_slow"],
                         "bound": c["meta"].get("bound"), "stable": c["stable"], "prediction_s": p,
                         "error": (p - c["native_median_s"]) / c["native_median_s"]})
        in_range = [r for r in rows if not r["too_slow"]]
        # Verdicts: per-problem weights fitted without that problem's intended solution.
        decisions = []
        for problem in problems:
            intended = cases[f"contest-{problem}"]
            w_p, _ = fit([(c["features"], c["native_median_s"]) for n, c in calibration.items()
                          if n != f"contest-{problem}"], names)
            intended_pred = predict(w_p, intended["features"])
            for name, c in heldout.items():
                if c["meta"]["problem"] != problem:
                    continue
                pred = predict(w_p, c["features"])
                for k in FACTORS:
                    limit = k * intended["native_median_s"]
                    ambiguous = c["native_min_s"] <= limit <= c["native_max_s"]
                    native_ac = c["native_median_s"] <= limit
                    for policy, model_ac in (("A", pred <= limit), ("R", pred <= k * intended_pred)):
                        decisions.append({"case": name, "problem": problem, "k": k, "policy": policy,
                                          "bound": c["meta"].get("bound"), "too_slow": c["meta"]["too_slow"],
                                          "ambiguous": ambiguous, "native_ac": native_ac, "model_ac": model_ac})

        def rates(subset):
            clear = [d for d in subset if not d["ambiguous"]]
            false_tle = sum(d["native_ac"] and not d["model_ac"] for d in clear)
            false_ac = sum(not d["native_ac"] and d["model_ac"] for d in clear)
            return {"decisions": len(clear), "ambiguous": len(subset) - len(clear),
                    "disagree": false_tle + false_ac, "false_tle": false_tle, "false_ac": false_ac,
                    "rate": (false_tle + false_ac) / len(clear) if clear else None}

        verdicts = {f"{policy}@{k}": rates([d for d in decisions if d["policy"] == policy and d["k"] == k])
                    for policy in ("A", "R") for k in FACTORS}
        report["models"][model] = {
            "features": names, "weights_s_per_unit": weights, "calibration_loss": loss,
            "calibration_worst_abs_error": max(abs(predict(weights, c["features"]) - c["native_median_s"]) /
                                               c["native_median_s"] for c in calibration.values()),
            "in_range_all": summary(in_range), "in_range_stable": summary([r for r in in_range if r["stable"]]),
            "too_slow": summary([r for r in rows if r["too_slow"]]),
            "verdicts": verdicts, "rows": rows, "decisions": decisions}
    m = report["models"]
    stable = {k: v["in_range_stable"] for k, v in m.items()}
    base = max(stable["M0"]["within_20pct"], stable["M1"]["within_20pct"])
    report["decision"] = {
        candidate: stable[candidate]["within_20pct"] > base and
        stable[candidate]["worst_abs_error"] <= stable["M1"]["worst_abs_error"]
        for candidate in ("M4", "ML4")}
    print("model calib_worst  in-range within20 all / stable   median  worst    R@2 disagree (FT/FA)   A@2")
    for k, v in m.items():
        a, s = v["in_range_all"], v["in_range_stable"]
        r2, a2 = v["verdicts"]["R@2.0"], v["verdicts"]["A@2.0"]
        print(f"{k:4} {v['calibration_worst_abs_error']:9.1%}   {a['within_20pct']:2}/{a['cases']:<2}  {s['within_20pct']:2}/{s['cases']:<2}"
              f"   {a['median_abs_error']:7.1%} {a['worst_abs_error']:7.1%}   {r2['disagree']:2}/{r2['decisions']:<3}"
              f" ({r2['false_tle']}/{r2['false_ac']})        {a2['disagree']}/{a2['decisions']} ({a2['false_tle']}/{a2['false_ac']})")
    print("decision:", report["decision"])
    if args.output:
        args.output.write_text(json.dumps(report, indent=1) + "\n")


if __name__ == "__main__":
    main()
