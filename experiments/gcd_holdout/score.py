"""Score the prospective GCD inputs from immutable diagnostic records and executable bytes."""

import argparse
from fractions import Fraction
import gzip
import hashlib
import json
from pathlib import Path
from statistics import median

from cost_model import baseline, estimate
from fit import FIT, ROOT
from study import rows

DATA = ROOT / "experiments/results/gce-gcd-controls/holdout"


def ppm(ratio):
    return round((ratio - 1) * 1_000_000)


def predictions(values, target):
    errors = [v / target - 1 for v in values]
    return {
        "ns": [round(v) for v in values],
        "median_ns": round(median(values)),
        "errors_ppm": [round(err * 1_000_000) for err in errors],
        "pass": all(abs(err) <= Fraction(1, 5) for err in errors),
    }


def score(dataset):
    fit = json.loads(FIT.read_bytes())
    samples = {}
    for row, case, _ in rows(dataset):
        samples.setdefault(case["workload_id"], []).append((row, case))
    if len(samples) != 5 or any(len(v) != 4 for v in samples.values()):
        raise ValueError(
            "expected four repetitions of each of the five registered Rust inputs"
        )
    cases = {}
    all_pairs = []
    for name, records in samples.items():
        native, fast, paired, adjusted, counts, pairs = [], [], [], [], [], []
        for row, case in sorted(records, key=lambda record: record[0]["repetition"]):
            native.extend(
                Fraction(row[phase]["cpu_time_ns"])
                for phase in ("native_before", "native_after")
            )
            fast.append(baseline(row["fast"], fit))
            base, model, observation = estimate(row["trips"], case, fit)
            paired.append(base)
            adjusted.append(model)
            counts.append(observation)
            pairs.append((row["fast"]["cpu_time_ns"], row["trips"]["cpu_time_ns"]))
        all_pairs.extend(pairs)
        target = median(native)
        spread = (max(native) - min(native)) / target
        cases[name] = {
            "native_ns": list(map(int, native)),
            "native_median_ns": str(target),
            "native_spread_ppm": round(spread * 1_000_000),
            "duration_pass": min(native) >= 20_000_000,
            "spread_pass": spread <= Fraction(1, 10),
            "counts": counts,
            "models": {
                "fast": predictions(fast, target),
                "paired_baseline": predictions(paired, target),
                "classified_loops": predictions(adjusted, target),
            },
            "probe_overhead_ppm": ppm(
                Fraction(sum(y for x, y in pairs), sum(x for x, y in pairs))
            ),
        }
    a, b = (cases["fibonacci_" + order] for order in ("alternating", "runs"))
    contrast = {
        "native_difference_ns": str(
            Fraction(a["native_median_ns"]) - Fraction(b["native_median_ns"])
        ),
        "native_endpoint_range_ns": [
            min(a["native_ns"]) - max(b["native_ns"]),
            max(a["native_ns"]) - min(b["native_ns"]),
        ],
        "estimate_difference_ns": {
            name: v["median_ns"] - b["models"][name]["median_ns"]
            for name, v in a["models"].items()
        },
    }
    return {
        "cases": cases,
        "alternating_minus_runs": contrast,
        "passes": {
            name: sum(c["models"][name]["pass"] for c in cases.values())
            for name in a["models"]
        },
        "instrumented_cpu": {
            "fast_ns": sum(x for x, y in all_pairs),
            "probe_ns": sum(y for x, y in all_pairs),
            "pooled_overhead_ppm": ppm(
                Fraction(sum(y for x, y in all_pairs), sum(x for x, y in all_pairs))
            ),
            "median_pair_overhead_ppm": ppm(
                median(Fraction(y, x) for x, y in all_pairs)
            ),
        },
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    raw = gzip.decompress((DATA / "dataset.json.gz").read_bytes())
    report = {
        "dataset_sha256": hashlib.sha256(raw).hexdigest(),
        "parameters_sha256": hashlib.sha256(FIT.read_bytes()).hexdigest(),
        **score(json.loads(raw)),
    }
    path = DATA / "evaluation.json"
    if args.verify:
        if json.loads(path.read_bytes()) != report:
            raise ValueError("published predictions and raw data disagree")
    else:
        path.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report["passes"]))
