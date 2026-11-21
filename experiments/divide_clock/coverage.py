#!/usr/bin/env python3
"""Compare the two local divider runs with each other and with the archived GCE production counters."""
import argparse
from fractions import Fraction
import json
from pathlib import Path
from statistics import median

from collect import read_observations
from corpus import HERE, ROOT, require, sha


def measures(production, weights):
    cycles = production["latency_bound"]["cycles_x16"]
    operand = production["memory_operand_bytes"]["bytes"]
    return {"instructions": production["instructions"], "latency_x16": cycles, "operand_bytes": operand,
            "baseline_ns": weights["latency_cycles"]*cycles/16 + weights["memory_bytes"]*operand}


def shapes(blocks):
    entries = {}
    for b in blocks:
        key = json.dumps({k:v for k,v in b.items() if k != "entries"}, sort_keys=True, separators=(",", ":"))
        require(key not in entries, "duplicate divider block shape")
        entries[key] = b["entries"]
    return entries


def results(cases):
    plan = json.loads((HERE/"protocol.json").read_bytes())
    raw = (ROOT/plan["reference"]).read_bytes()
    require(sha(raw) == plan["reference_sha256"], "changed baseline calibration")
    coeff = json.loads(raw)["models_on_medians"]["latency_memory"]["coefficients_ns"]
    weights = {k: Fraction(v) for k,v in coeff.items()}
    summary = {}
    for name, case in cases.items():
        gce = [measures(p, weights) for p in case["gce_production"]]
        local = [measures(r["production"], weights) for r in case["runs"]]
        diffs = {}
        native = case["native_ns"]
        native_center = median([Fraction(t) for t in native])
        require(native_center.denominator == 1, "GCE median is a fractional nanosecond")
        for metric in gce[0]:
            ref = median([Fraction(r[metric]) for r in gce])
            require(ref > 0, "zero reference counter")
            diffs[metric] = [round(1_000_000*(Fraction(v[metric])/ref-1)) for v in local]
        observed = [shapes(r["feature"]["blocks"]) for r in case["runs"]]
        require(len(observed) == 2, "expected two feature repeats")
        a,b = observed
        changed = set(a) ^ set(b)
        common = set(a) & set(b)
        different = [k for k in common if a[k] != b[k]]
        totals = [sum(v.values()) for v in observed]
        summary[name] = {"gce_native_ns": {"min": min(native), "median": int(native_center), "max": max(native)},
                         "gce_native_spread_ppm": round((max(native)-min(native))/native_center*1_000_000),
                         "local_minus_gce_median_ppm": diffs, "shape_counts": [len(a), len(b)],
                         "executed_divider_block_entries": totals, "shapes_with_changed_membership": len(changed),
                         "shared_shapes_with_changed_counts": len(different),
                         "sum_absolute_block_entry_deltas": sum(abs(a.get(k,0)-b.get(k,0)) for k in set(a)|set(b))}
    metrics = next(iter(summary.values()))["local_minus_gce_median_ppm"]
    maxima = {k: max(abs(x) for v in summary.values() for x in v["local_minus_gce_median_ppm"][k]) for k in metrics}
    return {"schema": 1, "max_absolute_local_minus_gce_median_ppm": maxima, "cases": summary}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("observations", type=Path)
    parser.add_argument("receipts", type=Path)
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--verify", type=Path)
    output.add_argument("--output", type=Path)
    args = parser.parse_args()
    computed = results(read_observations(args.observations, args.receipts))
    if args.verify:
        require(json.loads(args.verify.read_bytes()) == computed, "different feature coverage report")
        print("Verified original GCE baseline drift and same-ELF divider block counts for both local repeats.")
    elif args.output:
        args.output.write_text(json.dumps(computed, indent=2) + "\n")
    else:
        print(json.dumps(computed, indent=2))
