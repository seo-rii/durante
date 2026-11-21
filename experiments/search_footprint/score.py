"""Score every input of the pinned Rust footprint validation from its raw diagnostics."""

import argparse
from collections import defaultdict
from fractions import Fraction
import gzip
import hashlib
import json
from statistics import median

from model import (
    DATA,
    PLAN,
    baseline,
    calibrate,
    estimate,
    previous_estimate,
    span,
    uniform_distance,
)
from study import rows


def error(values, target):
    if not values or any(v is None for v in values):
        return {
            "pass": False,
            "ns": [None if v is None else round(v) for v in values],
            "median_ns": None,
        }
    errors = [v / target - 1 for v in values]
    return {
        "pass": all(abs(v) <= Fraction(1, 5) for v in errors),
        "ns": list(map(round, values)),
        "median_ns": round(median(values)),
        "error_ppm": [round(1_000_000 * v) for v in errors],
    }


def cpu(pairs):
    fast, sampled = map(sum, zip(*pairs))
    changes = [round(1_000_000 * (Fraction(y, x) - 1)) for x, y in pairs]
    return {
        "pairs": len(pairs),
        "fast_ns": fast,
        "sampled_ns": sampled,
        "pooled_overhead_ppm": round(1_000_000 * (Fraction(sampled, fast) - 1)),
        "median_pair_overhead_ppm": round(
            1_000_000 * (median(Fraction(y, x) for x, y in pairs) - 1)
        ),
        "pair_overhead_ppm": changes,
    }


def score(dataset):
    records = {}
    for row, case, _ in rows(dataset):
        records.setdefault(case["workload_id"], (case, []))[1].append(row)
    names = PLAN["calibration"] + PLAN["repeats"] + PLAN["new"]
    if set(records) != set(names) or any(len(rs) != 4 for _, rs in records.values()):
        raise ValueError("incomplete registered Rust footprint study")
    profile = calibrate(records, PLAN["calibration"])
    out, all_pairs = {}, []
    for name in names:
        case, reps = records[name]
        reps.sort(key=lambda r: r["repetition"])
        native = [
            Fraction(r[k]["cpu_time_ns"])
            for r in reps
            for k in ("native_before", "native_after")
        ]
        target = median(native)
        spread = (max(native) - min(native)) / target
        values = defaultdict(list)
        statuses, distances, spans, pairs = [], [], [], []
        for row in reps:
            o = row["sampled"]
            s = o["sample"]
            oracle = case["oracle"]
            if (
                s["suffix_histogram"] != oracle["sampled_suffix_histogram"]
                or s["sampled_min_index"] != oracle["sample_min_index"]
                or s["sampled_max_index"] != oracle["sample_max_index"]
                or s["visits"] != oracle["queries"]
            ):
                raise ValueError(
                    f"online sample disagrees with input replay for {name}"
                )
            prediction, status = estimate(o, case["binary_sha256"], profile)
            values["fast"].append(baseline(row["fast"]))
            values["paired_baseline"].append(baseline(o))
            values["previous_suffix"].append(previous_estimate(o))
            values["footprint"].append(prediction)
            statuses.append(status)
            distances.append(round(1_000_000 * uniform_distance(s)))
            spans.append(span(s))
            pairs.append((row["fast"]["cpu_time_ns"], o["cpu_time_ns"]))
        out[name] = {
            "native_ns": list(map(int, native)),
            "median_ns": str(target),
            "spread_ppm": round(1_000_000 * spread),
            "duration_pass": min(native) >= 20_000_000,
            "spread_pass": spread <= Fraction(1, 10),
            "span_bytes": spans,
            "uniform_distance_ppm": distances,
            "status": statuses,
            "cpu": cpu(pairs),
            "models": {m: error(v, target) for m, v in values.items()},
        }
        all_pairs.extend(pairs)
    sensitivity = {}
    for omit in PLAN["calibration"]:
        candidate = calibrate(records, [n for n in PLAN["calibration"] if n != omit])
        case, reps = records[omit]
        predictions = [
            estimate(r["sampled"], case["binary_sha256"], candidate) for r in reps
        ]
        sensitivity[omit] = {
            "status": [s for _, s in predictions],
            **error([v for v, _ in predictions], Fraction(out[omit]["median_ns"])),
        }
    return profile, {
        "cases": out,
        "cpu": cpu(all_pairs),
        "omitted_calibration": sensitivity,
        "passes": {
            group: {
                m: sum(out[n]["models"][m]["pass"] for n in PLAN[group])
                for m in ("fast", "paired_baseline", "previous_suffix", "footprint")
            }
            for group in ("calibration", "repeats", "new")
        },
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    raw = gzip.decompress((DATA / "dataset.json.gz").read_bytes())
    profile, result = score(json.loads(raw))
    result = {"dataset_sha256": hashlib.sha256(raw).hexdigest(), **result}
    for filename, value in (("parameters.json", profile), ("evaluation.json", result)):
        path = DATA / filename
        if args.verify:
            if json.loads(path.read_bytes()) != value:
                raise ValueError(f"independent replay differs from {filename}")
        else:
            path.write_text(json.dumps(value, indent=2) + "\n")
    print(json.dumps(result["passes"]))
