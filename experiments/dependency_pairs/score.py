#!/usr/bin/env python3
"""Compare dependencies using the frozen Rust clock and exact native CPU brackets."""
import argparse
from collections import defaultdict
from fractions import Fraction
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
from statistics import median

from study import validated_rows

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
spec = importlib.util.spec_from_file_location("model_limit", HERE.parent / "rust_operand_bytes/model_limit.py")
limit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(limit)


def middle(numbers):
    return median([Fraction(n) for n in numbers])


def ppm(value):
    return round(value * 1_000_000)


def stats(numbers):
    return {key: str(value) for key, value in (("min", min(numbers)), ("median", middle(numbers)), ("max", max(numbers)))}


def summarize(rows, weights, gates):
    cpu = [row[phase]["cpu_time_ns"] for row in rows for phase in ("native_before", "native_after")]
    counters = [r["operand_bytes"]["production"] for r in rows]
    values = {
        "instructions": [c["instructions"] for c in counters],
        "latency_cycles": [Fraction(c["latency_bound"]["cycles_x16"], 16) for c in counters],
        "memory_bytes": [c["memory_operand_bytes"]["bytes"] for c in counters],
        "rep_bytes": [c["rep_bytes"] for c in counters],
    }
    predicted = [sum(Fraction(w) * values[feature][i] for feature, w in weights.items()) for i in range(len(rows))]
    target = middle(cpu)
    errors = [p / target - 1 for p in predicted]
    spread = Fraction(max(cpu) - min(cpu), target)
    return {
        "native_min_ns": min(cpu), "native_median_ns": str(target), "native_max_ns": max(cpu),
        "native_spread_ppm": ppm(spread),
        "duration_pass": min(cpu) >= gates["min_native_ns"],
        "spread_pass": spread <= Fraction(gates["max_spread_ppm"], 1_000_000),
        "features": {field: stats(v) for field, v in values.items()}, "predicted_ns": stats(predicted),
        "errors_ppm": [ppm(e) for e in errors],
        "accuracy_pass": max(map(abs, errors)) <= Fraction(gates["max_error_ppm"], 1_000_000),
        "round_native_midpoints_ns": [str(middle((r["native_before"]["cpu_time_ns"], r["native_after"]["cpu_time_ns"])))
                                      for r in rows],
    }


def contrast(left, right):
    def ratio(a, b):
        return str(Fraction(a) / Fraction(b)) if Fraction(b) else None

    features = ("instructions", "latency_cycles", "memory_bytes")
    return {
        "native_median_ratio": ratio(left["native_median_ns"], right["native_median_ns"]),
        "native_ratio_range": [ratio(left["native_min_ns"], right["native_max_ns"]),
                               ratio(left["native_max_ns"], right["native_min_ns"])],
        "round_native_ratios": [ratio(a, b) for a, b in zip(left["round_native_midpoints_ns"],
                                                           right["round_native_midpoints_ns"], strict=True)],
        "predicted_median_ratio": ratio(left["predicted_ns"]["median"], right["predicted_ns"]["median"]),
        "feature_median_ratios": {n: ratio(left["features"][n]["median"], right["features"][n]["median"])
                                  for n in features},
    }


def report(dataset, protocol):
    source = (ROOT / protocol["clock"]["evaluation"]).read_bytes()
    if hashlib.sha256(source).hexdigest() != protocol["clock"]["sha256"]:
        raise ValueError("the frozen calibration changed")
    weights = json.loads(source)["models_on_medians"]["latency_memory"]["coefficients_ns"]
    if weights != protocol["clock"]["coefficients_ns"]:
        raise ValueError("the coefficients disagree with the registered clock")
    grouped = defaultdict(list)
    for _, row in validated_rows(dataset, protocol):
        grouped[row["workload_id"]].append(row)
    if any(len(rows) != 4 for rows in grouped.values()) or len(grouped) != 8:
        raise ValueError("expected four triplets for each of eight fixtures")
    cases = {name: summarize(rows, weights, protocol["thresholds"]) for name, rows in grouped.items()}
    for fixture in protocol["cases"]:
        case = cases[fixture["workload_id"]]
        case["expected_counts"] = {"instructions": fixture["expected_instructions"],
                                   "memory_bytes": fixture["expected_operand_bytes"]}
        case["matches_expected_counts"] = all(
            Fraction(case["features"][key]["min"]) == Fraction(case["features"][key]["max"]) == value
            for key, value in case["expected_counts"].items()
        )
    pairs = {}
    for name, (a, b) in protocol["comparisons"].items():
        pair = {a: cases[a], b: cases[b]}
        pairs[name] = {"numerator": a, "denominator": b, **contrast(cases[a], cases[b]),
                       "optimistic_minimax": limit.minimum_worst_error(pair, optimistic=True)}
    return {"cases": cases, "comparisons": pairs, "coefficients_ns": weights,
            "accuracy_passes": sum(row["accuracy_pass"] for row in cases.values()),
            "optimistic_minimax": limit.minimum_worst_error(cases, optimistic=True),
            "median_minimax": limit.minimum_worst_error(cases, optimistic=False)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--output", type=Path)
    mode.add_argument("--verify", type=Path)
    args = parser.parse_args()
    raw = args.dataset.read_bytes()
    if raw.startswith(b"\x1f\x8b"):
        raw = gzip.decompress(raw)
    data = json.loads(raw)
    plan = (HERE / "protocol.json").read_bytes()
    if hashlib.sha256(plan).hexdigest() != data["protocol_sha256"]:
        raise ValueError("different dependency experiment protocol")
    result = {**report(data, json.loads(plan)), "schema": 1, "dataset_sha256": hashlib.sha256(raw).hexdigest(),
              "protocol_sha256": data["protocol_sha256"]}
    if args.verify:
        if json.loads(args.verify.read_bytes()) != result:
            raise ValueError("saved evaluation differs from replay")
        print("Verified eight cases, four native brackets per case, all dependency contrasts and model bounds.")
    elif args.output:
        args.output.write_text(json.dumps(result, indent=2) + "\n")
    else:
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
