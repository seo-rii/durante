#!/usr/bin/env python3
"""Score the frozen reference clock and two penalties fitted only to paired load controls."""
import argparse
from collections import defaultdict
from fractions import Fraction
import gzip
import hashlib
import json
from pathlib import Path
from statistics import median

from study import validated_rows

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def center(values):
    return median([Fraction(value) for value in values])


def ppm(value):
    return round(value * 1_000_000)


def span(values):
    return {key: str(val) for key, val in (("min", min(values)), ("median", center(values)), ("max", max(values)))}


def load_features(phase, weights):
    sample = phase["sample"]
    prod = phase["production"]
    observed = [s for s in sample["sites"] if s["pairs"]]
    covered = sum(s["loads"] for s in observed)
    far = sum((Fraction(s["loads"] * s["far_pairs"], s["pairs"]) for s in observed), Fraction(0))
    cycles = Fraction(prod["latency_bound"]["cycles_x16"], 16)
    baseline = cycles * weights["latency_cycles"] + prod["memory_operand_bytes"]["bytes"] * weights["memory_bytes"]
    scale = min(Fraction(1), cycles / (5 * sample["loads"])) if sample["loads"] else Fraction(0)
    return {"baseline": baseline, "far": far, "scaled_far": far * scale,
            "eligible_loads": sample["loads"], "unpaired_loads": sample["loads"] - covered,
            "pairs": sample["pairs"], "far_pairs": sample["far_pairs"], "sites": len(sample["sites"]),
            "far_share": far / covered if covered else Fraction(0)}


def fit_penalty(cases, training, feature):
    """Least squared relative errors with a fixed baseline and one nonnegative free coefficient."""
    cross, squares = Fraction(0), Fraction(0)
    for name in training:
        case = cases[name]
        target = center(case["native"])
        x = center([obs[feature] for obs in case["features"]]) / target
        residual = 1 - center([obs["baseline"] for obs in case["features"]]) / target
        cross += x * residual
        squares += x * x
    return max(Fraction(0), cross / squares) if squares else Fraction(0)


def score(cases, selected, feature, weight, tolerance):
    errors, estimates, passed = {}, {}, []
    for name in selected:
        case = cases[name]
        predicted = [v["baseline"] + weight * v[feature] for v in case["features"]]
        target = center(case["native"])
        err = [n / target - 1 for n in predicted]
        errors[name] = [ppm(e) for e in err]
        estimates[name] = span(predicted)
        if max(map(abs, err)) <= tolerance:
            passed.append(name)
    return {"passed": passed, "count": len(passed), "cases": len(selected),
            "estimates_ns": estimates, "errors_ppm": errors}


def timing(pairs):
    base = sum(x for x, _, _ in pairs)
    sampled = sum(y for _, y, _ in pairs)
    return {"pairs": len(pairs), "baseline_cpu_ns": base, "sampled_cpu_ns": sampled,
            "pooled_ratio_ppm": ppm(Fraction(sampled, base)),
            "median_ratio_ppm": ppm(center(Fraction(y, x) for x, y, _ in pairs))}


def evaluate(data, plan):
    calibration = (ROOT / plan["clock"]["evaluation"]).read_bytes()
    if hashlib.sha256(calibration).hexdigest() != plan["clock"]["sha256"]:
        raise ValueError("wrong historical calibration")
    original = json.loads(calibration)["models_on_medians"]["latency_memory"]["coefficients_ns"]
    if original != plan["clock"]["coefficients_ns"]:
        raise ValueError("wrong frozen clock coefficients")
    weights = {key: Fraction(val) for key, val in original.items()}
    controls, rust = plan["train_controls"], plan["rust_evaluation"]
    tolerance = Fraction(plan["thresholds"]["max_error_ppm"], 1_000_000)
    cases = defaultdict(lambda: {"native": [], "features": [], "time": [], "counter_delta_ppm": []})
    for _, row in validated_rows(data, plan):
        case = cases[row["workload_id"]]
        case["native"].extend(row[phase]["cpu_time_ns"] for phase in ("native_before", "native_after"))
        case["time"].append((row["baseline"]["cpu_time_ns"], row["sampled"]["cpu_time_ns"], row["phase_order"][1]))
        case["features"].append(load_features(row["sampled"], weights))
        prod_a, prod_b = row["baseline"]["production"], row["sampled"]["production"]
        for key, field in (("instructions", None), ("latency_bound", "cycles_x16"), ("memory_operand_bytes", "bytes")):
            a, b = (p[key] if field is None else p[key][field] for p in (prod_a, prod_b))
            if a:
                case["counter_delta_ppm"].append((key, ppm(Fraction(b-a, a))))
    if set(cases) != set(controls + rust) or set(controls) & set(rust):
        raise ValueError("invalid split")

    summaries = {}
    for name, v in cases.items():
        native = v["native"]
        spread = Fraction(max(native)-min(native), center(native))
        summaries[name] = {
            "native_ns": span(native), "native_spread_ppm": ppm(spread),
            "duration_pass": min(native) >= plan["thresholds"]["min_native_ns"],
            "spread_pass": spread <= Fraction(plan["thresholds"]["max_spread_ppm"], 1_000_000),
            "features": {k: span([r[k] for r in v["features"]]) for k in v["features"][0]},
            "instrumented_cpu": timing(v["time"]),
            "max_abs_counter_delta_ppm": {k: max((abs(n) for key, n in v["counter_delta_ppm"] if key == k), default=None)
                                           for k in ("instructions", "latency_bound", "memory_operand_bytes")}}
    models = {}
    for model in ("baseline", "far", "scaled_far"):
        w = Fraction(0) if model == "baseline" else fit_penalty(cases, controls, model)
        results = {"ns_per_unit": str(w), "controls": score(cases, controls, model, w, tolerance),
                   "rust": score(cases, rust, model, w, tolerance), "lane_holdout": {}}
        for lane in ("serial", "parallel"):
            train = [n for n in controls if n.endswith(lane)]
            test = [n for n in controls if not n.endswith(lane)]
            w_lane = Fraction(0) if model == "baseline" else fit_penalty(cases, train, model)
            results["lane_holdout"][lane+"_train"] = {"ns_per_unit": str(w_lane),
                                                     **score(cases, test, model, w_lane, tolerance)}
        models[model] = results
    separation = {}
    for lane in ("serial", "parallel"):
        adjacent, scattered = [cases[f"load_{order}_{lane}"] for order in ("adjacent", "scattered")]
        lowest = min(v["far_share"] for v in scattered["features"])
        highest = max(v["far_share"] for v in adjacent["features"])
        separation[lane] = {"separated": lowest > highest, "min_scattered_minus_max_adjacent_ppm": ppm(lowest-highest)}
    times = [t for v in cases.values() for t in v["time"]]
    cost = {"all": timing(times), "controls": timing([t for n in controls for t in cases[n]["time"]]),
            "rust": timing([t for n in rust for t in cases[n]["time"]])}
    for first in ("baseline", "sampled"):
        cost[first+"_first"] = timing([t for t in times if t[2] == first])
    return {"cases": summaries, "separation": separation, "models": models, "instrumented_cpu": cost}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    choose = parser.add_mutually_exclusive_group()
    choose.add_argument("--output", type=Path)
    choose.add_argument("--verify", type=Path)
    args = parser.parse_args()
    raw = args.dataset.read_bytes()
    if raw.startswith(b"\x1f\x8b"):
        raw = gzip.decompress(raw)
    data = json.loads(raw)
    raw_plan = (HERE / "protocol.json").read_bytes()
    if data["protocol_sha256"] != hashlib.sha256(raw_plan).hexdigest():
        raise ValueError("different registered experiment")
    result = {**evaluate(data, json.loads(raw_plan)), "schema": 1, "protocol_sha256": data["protocol_sha256"],
              "dataset_sha256": hashlib.sha256(raw).hexdigest()}
    if args.verify:
        if json.loads(args.verify.read_bytes()) != result:
            raise ValueError("saved evaluation differs")
        print("Verified sampling signal, paired diagnostic cost, control fits and eight Rust evaluations.")
    elif args.output:
        args.output.write_text(json.dumps(result, indent=2) + "\n")
    else:
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
