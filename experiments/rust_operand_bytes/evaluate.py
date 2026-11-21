#!/usr/bin/env python3
"""Replay the preregistered paired operand-byte clock and measurement-cost comparisons."""
import argparse
from collections import defaultdict
from fractions import Fraction
import gzip
import hashlib
import json
from pathlib import Path
from statistics import median
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "rust_instruction_mix"))
from score import center, digest, fit, ppm, relative_spread, require, uint


def sha(data):
    return hashlib.sha256(data).hexdigest()


def children(dataset, plan):
    cases = plan["cases"]
    require(len(cases) == 18 and plan["repetitions"] == 3 and
            dataset["schema"] == 1 and len(dataset["samples"]) == 54 and dataset["complete"] is True and
            uint(dataset["cpu"], 1) and dataset["cpu"] == 1 and uint(dataset["collector_pid"], 1),
            "expected 54 completed CPU-pinned quartets")
    require(set(dataset["programs"]) == {c["workload_id"] for c in cases} and
            set(dataset["tools"]) == {"launcher", "meter", "runtime", "production_reader"} and
            all(digest(h) for h in dataset["tools"].values()), "wrong program or measurement tool set")
    pids = {dataset["collector_pid"]}
    indices = list(range(len(cases)))
    expected = [(rep, i) for rep, offset in enumerate(plan["round_rotations"])
                for i in indices[offset:] + indices[:offset]]
    for sequence, (row, (rep, i)) in enumerate(zip(dataset["samples"], expected, strict=True), 1):
        case = cases[i]
        require(row["repetition"] == rep + 1 and all(row[k] == v for k, v in case.items()),
                "case order or frozen fixture digest changed")
        compiled = dataset["programs"][row["workload_id"]]
        require(all(compiled[k] == case[k] for k in ("source_sha256", "binary_sha256")), "ELF identity mismatch")
        modes = ["latency_only", "operand_bytes"] if (rep+i) % 2 == 0 else ["operand_bytes", "latency_only"]
        order = ["native_before", *modes, "native_after"]
        require(row["phase_order"] == order, "diagnostic mode order mismatch")
        for phase in order:
            obs = row[phase]
            require(uint(obs["pid"], 1) and obs["pid"] not in pids and uint(obs["exit_code"]) and
                    obs["exit_code"] == 0 and obs["stdout_sha256"] == case["expected_sha256"],
                    "child identity, exit or output mismatch")
            pids.add(obs["pid"])
            require(uint(obs["user_ns"]) and uint(obs["system_ns"]) and uint(obs["wall_ns"], 1) and
                    uint(obs["cpu_time_ns"], 1) and obs["cpu_time_ns"] == obs["user_ns"] + obs["system_ns"],
                    "inconsistent final child CPU accounting")
        for mode in modes:
            prod = row[mode]["production"]
            require(prod["complete"] is True and prod["valid"] is True and prod["quality"] == "uninterrupted" and
                    all(uint(prod[k]) and prod[k] == 1 for k in ("process_count", "image_count", "threads")) and
                    prod["cpuid_profile"] == "x86-64-v2" and
                    all(uint(prod[k]) and prod[k] == 0 for k in ("unsupported_rep_entries", "signals_observed")),
                    "incomplete diagnostic receipt")
            bound = prod["latency_bound"]
            require(set(bound) == {"version", "cycles_x16"} and uint(bound["version"]) and bound["version"] == 1 and
                    uint(prod["instructions"], 1) and uint(bound["cycles_x16"]) and
                    bound["cycles_x16"] >= 4 * prod["instructions"], "invalid latency or instruction count")
            require(all(uint(prod[k]) for k in ("rep_bytes", "rep_movs_bytes", "rep_stos_bytes")) and
                    prod["rep_bytes"] == prod["rep_movs_bytes"] + prod["rep_stos_bytes"], "invalid REP counts")
            require(("memory_operand_bytes" in prod) == (mode == "operand_bytes"), "optional counter mismatch")
            if mode == "operand_bytes":
                mem = prod["memory_operand_bytes"]
                require(set(mem) == {"version", "bytes"} and uint(mem["version"]) and mem["version"] == 1 and
                        uint(mem["bytes"], 1),
                        "invalid memory operand counter")
        yield sequence, row


def observations(dataset, plan):
    result = defaultdict(lambda: {"native": [], "features": [], "pairs": []})
    for _, row in children(dataset, plan):
        case = result[(row["workload_id"], row["scale"])]
        case["native"].extend(row[p]["cpu_time_ns"] for p in ("native_before", "native_after"))
        a, b = [row[p]["production"] for p in ("latency_only", "operand_bytes")]
        case["features"].append({"instructions": b["instructions"], "rep_bytes": b["rep_bytes"],
                                 "latency_cycles": Fraction(b["latency_bound"]["cycles_x16"], 16),
                                 "memory_bytes": b["memory_operand_bytes"]["bytes"]})
        case["pairs"].append({"first": row["phase_order"][1], "baseline_ns": row["latency_only"]["cpu_time_ns"],
                              "enabled_ns": row["operand_bytes"]["cpu_time_ns"],
                              "instruction_delta_ppm": ppm(Fraction(b["instructions"], a["instructions"])-1),
                              "latency_delta_ppm": ppm(Fraction(b["latency_bound"]["cycles_x16"],
                                                               a["latency_bound"]["cycles_x16"])-1)})
    return result


def ratios(pairs):
    values = [Fraction(pair["enabled_ns"], pair["baseline_ns"]) for pair in pairs]
    return {"pairs": len(values), "median_enabled_to_latency_ppm": ppm(median(values)),
            "pooled_enabled_to_latency_ppm": ppm(Fraction(sum(p["enabled_ns"] for p in pairs),
                                                         sum(p["baseline_ns"] for p in pairs)))}


def label(key):
    return f"{key[0]}/{key[1]}"


def score_cases(points, targets, weights, subset, limit):
    errors = {}
    passed = 0
    worst = ("", Fraction(0))
    for key in subset:
        key_name = label(key)
        exact = [sum(counts[col]*w for col, w in weights.items())/targets[key]-1
                 for counts in points[key]["features"]]
        errors[key_name] = [ppm(e) for e in exact]
        peak = max(exact, key=abs)
        passed += abs(peak) <= limit
        if not worst[0] or abs(peak) > abs(worst[1]):
            worst = key_name, peak
    return {"cases": len(subset), "passed": passed, "worst_case": worst[0],
            "worst_signed_error_ppm": ppm(worst[1]), "signed_errors_ppm": errors}


def score_models(points, plan, targets):
    original = plan["train"] + plan["validation"]
    train = [key for key in points if key[0] in plan["train"]]
    validation = [key for key in points if key[0] in plan["validation"]]
    transfer = [key for key in points if key[0] in plan["transfer"]]
    medians = {key: {n: center([row[n] for row in point["features"]])
                     for n in point["features"][0]} for key, point in points.items()}
    limit = Fraction(plan["thresholds"]["max_error_ppm"], 1_000_000)
    models = {}
    for name, columns in plan["models"].items():
        def solve(keys):
            return fit([(medians[k], targets[k]) for k in keys], columns)
        weights, rank = solve(train)
        rows = {"coefficients_ns": {n: str(w) for n, w in weights.items()}, "rank": rank,
                "train": score_cases(points, targets, weights, train, limit),
                "validation": score_cases(points, targets, weights, validation, limit),
                "transfer": score_cases(points, targets, weights, transfer, limit)}
        folds = {}
        for held in original:
            train_fold = [k for k in points if k[0] in original and k[0] != held]
            test_fold = [k for k in points if k[0] == held]
            w, _ = solve(train_fold)
            folds[held] = score_cases(points, targets, w, test_fold, limit)
        worst = max(folds.values(), key=lambda fold: abs(fold["worst_signed_error_ppm"]))
        rows["leave_one_program_out"] = {"passed": sum(f["passed"] for f in folds.values()), "cases": 15,
                                         "worst_case": worst["worst_case"],
                                         "worst_signed_error_ppm": worst["worst_signed_error_ppm"],
                                         "folds": folds}
        models[name] = rows
    return models


def report(dataset, plan):
    points = observations(dataset, plan)
    medians = {k: center(v["native"]) for k, v in points.items()}
    minima = {k: min(v["native"]) for k, v in points.items()}
    pairs = [p for v in points.values() for p in v["pairs"]]
    cases = {}
    for k, point in points.items():
        native = point["native"]
        spread = relative_spread(native)
        spread_on_min = Fraction(max(native) - min(native), min(native))
        max_spread = Fraction(plan["thresholds"]["max_native_spread_ppm"], 1_000_000)
        stats = {}
        for name in point["features"][0]:
            counts = [r[name] for r in point["features"]]
            stats[name] = {"min": str(min(counts)), "median": str(center(counts)), "max": str(max(counts))}
        cases[label(k)] = {"native_median_ns": float(medians[k]), "native_min_ns": minima[k],
                           "native_max_ns": max(native), "native_spread_ppm": ppm(spread),
                           "native_floor_pass": minima[k] >= plan["thresholds"]["min_native_ns"],
                           "native_spread_pass": spread <= max_spread,
                           "native_spread_on_min_ppm": ppm(spread_on_min),
                           "native_spread_on_min_pass": spread_on_min <= max_spread,
                           "min_vs_median_ppm": ppm(minima[k]/medians[k]-1),
                           "features": stats, "cost": ratios(point["pairs"])}
    cost = ratios(pairs)
    for first in ("latency_only", "operand_bytes"):
        cost[first+"_first"] = ratios([p for p in pairs if p["first"] == first])
    cost["baseline_cpu_ns"] = sum(p["baseline_ns"] for p in pairs)
    cost["enabled_cpu_ns"] = sum(p["enabled_ns"] for p in pairs)
    for name in ("instruction_delta_ppm", "latency_delta_ppm"):
        cost["max_abs_"+name] = max(abs(p[name]) for p in pairs)
    return {"schema": 1, "quartets": len(dataset["samples"]), "cost": cost, "cases": cases,
            "models_on_medians": score_models(points, plan, medians),
            "models_on_minima": score_models(points, plan, minima)}


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
    protocol = (HERE / "protocol.json").read_bytes()
    require(data["protocol_sha256"] == sha(protocol), "different protocol")
    require(data["collector_sha256"] == sha((HERE/"collect.py").read_bytes()) and
            data["shared_collector_sha256"] == sha((HERE.parent/"rust_instruction_mix/collect.py").read_bytes()),
            "different collector source")
    result = report(data, json.loads(protocol))
    result.update(dataset_sha256=sha(raw), protocol_sha256=sha(protocol))
    if args.verify:
        require(result == json.loads(args.verify.read_bytes()), "saved evaluation differs from replay")
        print("Verified 54 quartets, four models, five held-out-family folds and minimum sensitivity.")
    elif args.output:
        args.output.write_text(json.dumps(result, indent=2, allow_nan=False)+"\n")
    else:
        print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
