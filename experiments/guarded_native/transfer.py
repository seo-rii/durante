"""Evaluate single-native-run anchors and coefficient-independent error bounds from matched observations."""
import argparse
from fractions import Fraction
import gzip
import json
from math import ceil
from pathlib import Path
from statistics import median
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from guarded_native.evidence import evidence
from guarded_native.score import MS, RESULT, native_samples, require, score_case, summarize, views


def describe(ns):
    center = median(ns)
    return {"min_ns": min(ns), "median_ns": center, "max_ns": max(ns),
            "quality_pass": min(ns) >= 20 * MS and 10 * (max(ns) - min(ns)) <= center}


def first_jobs(rows, cases):
    """Resolve the first native process of each ELF and its unique, same-input full meter record."""
    first = {}
    for row in rows:
        name = row["case_id"]
        case = cases[name]
        elf = case["binary_sha256"]
        if elf in first:
            continue
        require(row["arm"] == "warmup" and row["rep"] == 0 and len(case["rows"]) == 1
                and case["rows"][0]["kind"] == "first", "wrong calibration launch: " + name)
        first[elf] = {"case": name, "native_cpu_ns": row["cpu_us"] * 1000, "native_pid": row["launcher_pid"],
                      "metered_pid": case["rows"][0]["full"]["pid"]}
    require(len(first) == 7, "missing native calibration")
    return first


def work(obs, weights):
    return sum(obs[k] * w for k, w in weights.items())


def scaled_ns(anchor, current, calibration):
    require(anchor > 0 and calibration > 0 and current >= 0, "invalid anchor or work")
    return ceil(Fraction(anchor) * current / calibration)


def anchored_scores(source, native, first):
    cases = source["cases"]
    weights = {k: Fraction(v) for k, v in source["combined_reference_weights"].items()}
    require(set(weights) == {"instructions", "rep_bytes"} and all(w > 0 for w in weights.values()),
            "expected the positive, frozen combined-REP reference")
    results = {k: {} for k in ("frozen", "anchor_only", "first_native", "archived_native")}
    for name, case in cases.items():
        anchor = first[case["binary_sha256"]]
        start = anchor["case"]
        if name == start:
            continue
        base = work(cases[start]["rows"][0]["full"], weights)
        jobs = [work(obs, weights) for obs in views(case)["delivered"]]
        duration = anchor["native_cpu_ns"]
        predictions = {"frozen": [ceil(w) for w in jobs], "anchor_only": [duration] * len(jobs),
                       "first_native": [scaled_ns(duration, w, base) for w in jobs]}
        older = cases[start]["archived_native_ns"]
        if older:
            predictions["archived_native"] = [scaled_ns(median(older), w, base) for w in jobs]
        for method, ns in predictions.items():
            results[method][name] = score_case(native[name], ns, [])
    out = {}
    for method, rows in results.items():
        groups = {pop: [n for n in rows if cases[n]["population"] == pop] for pop in ("real", "control")}
        out[method] = {"cases": rows, "summary": {k: summarize(rows, names) for k, names in groups.items() if names}}
    return out


def pair_certificate(a_native, b_native, a_max, b_min):
    """Bound the smaller possible worst error even with favorable counts and native timings."""
    if a_native <= b_native:
        return None
    ratios = {}
    for k in a_max:
        if b_min[k] == 0:
            if a_max[k] > 0:
                return None
        else:
            ratios[k] = Fraction(a_max[k], b_min[k])
    r = max([Fraction(1), *ratios.values()])  # A nonnegative intercept contributes ratio 1.
    error = (a_native - r * b_native - 1) / (a_native + r * b_native)
    if error <= 0:
        return None
    return error, {"slower_min_ns": a_native, "faster_max_ns": b_native, "max_work_ratio": str(r),
                   "slower_max_features": a_max, "faster_min_features": b_min,
                   "lower_bound": str(error), "excludes_20pct": 5 * error > 1}


def witnesses(cases, native, design):
    real = sorted(n for n in cases if cases[n]["population"] == "real")
    pools = {"heldout_real_cross_family": [n for n in real if cases[n]["program"] in design["case_groups"]["heldout_real"]],
             "real_cross_family": real, "real_within_family": real,
             "quality_real_cross_family": [n for n in real if describe(native[n])["quality_pass"]],
             "controls": sorted(set(cases) - set(real))}
    models = {**design["fit_models"], "expanded": ["instructions", "movs_bytes", "stos_bytes", "loads", "pairs",
                                                  "near_pairs", "far_pairs", "max_span_bytes"]}
    out = {}
    for view in ("delivered", "full"):
        rows = {n: views(c)[view] for n, c in cases.items()}
        out[view] = {}
        for model, columns in models.items():
            high = {n: {k: max(v[k] for v in vs) for k in columns} for n, vs in rows.items()}
            low = {n: {k: min(v[k] for v in vs) for k in columns} for n, vs in rows.items()}
            strongest = {}
            for pool, names in pools.items():
                best, value = None, 0
                for a in names:
                    for b in names:
                        same = cases[a]["program"] == cases[b]["program"]
                        if a == b or (pool != "controls" and same != (pool == "real_within_family")):
                            continue
                        candidate = pair_certificate(min(native[a]), max(native[b]), high[a], low[b])
                        if candidate and candidate[0] > value:
                            value, fields = candidate
                            best = {"slower": a, "faster": b, **fields}
                strongest[pool] = best
            out[view][model] = strongest
    return out


def evaluate(path):
    data = json.loads(gzip.decompress(path.read_bytes()) if path.suffix == ".gz" else path.read_bytes())
    source = evidence()
    cases = source["cases"]
    native = native_samples(data, cases)
    first = first_jobs(data["rows"], cases)
    across = {}
    for name, case in cases.items():
        if case["archived_native_ns"]:
            historical = describe(case["archived_native_ns"])
            fresh = describe(native[name])
            across[name] = {"historical": historical, "fresh": fresh,
                            "median_ratio": str(Fraction(fresh["median_ns"], historical["median_ns"])),
                            "overlap": max(fresh["min_ns"], historical["min_ns"]) <=
                                       min(fresh["max_ns"], historical["max_ns"])}
    design = json.loads((HERE / "design.json").read_bytes())
    return {"schema": 1, "protocol_sha256": data["protocol_sha256"], "first_jobs_by_elf": first,
            "within_elf": anchored_scores(source, native, first), "across_guests": across,
            "pair_bounds": witnesses(cases, native, design)}


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("dataset", type=Path, default=RESULT / "dataset.json.gz", nargs="?")
    dest = cli.add_mutually_exclusive_group()
    dest.add_argument("--output", type=Path)
    dest.add_argument("--verify", type=Path)
    args = cli.parse_args()
    result = evaluate(args.dataset)
    if args.verify:
        require(result == json.loads(args.verify.read_bytes()), "saved transfer analysis differs")
        print("Verified native anchors, historical timings and affine error bounds.")
    elif args.output:
        args.output.write_text(json.dumps(result, indent=2) + "\n")
    else:
        print(json.dumps(result, indent=2))
