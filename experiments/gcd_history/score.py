"""Replay the prospective predictor comparison from the third guest's receipts."""

import argparse
from collections import defaultdict
from fractions import Fraction
import gzip
import hashlib
import json
from pathlib import Path
from statistics import median

from study import rows
from model import FIT, baseline, predict

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "experiments/results/gce-gcd-history"


def predictions(values, target):
    errors = [v / target - 1 for v in values]
    return {
        "ns": [round(v) for v in values],
        "median_ns": round(median(values)),
        "error_ppm": [round(e * 1_000_000) for e in errors],
        "pass": all(abs(e) <= Fraction(1, 5) for e in errors),
    }


def overhead(pairs):
    fast, probe = map(sum, zip(*pairs))
    return {
        "fast_ns": fast,
        "probe_ns": probe,
        "pairs": len(pairs),
        "pooled_increase_ppm": round((Fraction(probe, fast) - 1) * 1_000_000),
        "median_pair_increase_ppm": round(
            (median(Fraction(y, x) for x, y in pairs) - 1) * 1_000_000
        ),
    }


def score(dataset):
    study = json.loads((Path(__file__).with_name("evaluation-plan.json")).read_bytes())
    grouped = defaultdict(list)
    for row, case, _ in rows(dataset):
        grouped[case["workload_id"]].append((row, case))
    if set(grouped) != set(study["anchors"] + study["prospective"]) or any(
        len(v) != 4 for v in grouped.values()
    ):
        raise ValueError("incomplete six-case history comparison")
    cases, total_pairs = {}, []
    for name, records in grouped.items():
        models = defaultdict(list)
        native, observed, pairs = [], [], []
        for row, case in sorted(records, key=lambda r: r[0]["repetition"]):
            native.extend(
                Fraction(row[k]["cpu_time_ns"])
                for k in ("native_before", "native_after")
            )
            models["fast"].append(baseline(row["fast"], FIT))
            estimates, counts = predict(row["trips"], case)
            for model, value in estimates.items():
                models[model].append(value)
            observed.append(counts)
            pairs.append((row["fast"]["cpu_time_ns"], row["trips"]["cpu_time_ns"]))
        target = median(native)
        spread = (max(native) - min(native)) / target
        cases[name] = {
            "native_ns": list(map(int, native)),
            "median_ns": str(target),
            "spread_ppm": round(spread * 1_000_000),
            "duration_pass": min(native) >= 20_000_000,
            "spread_pass": spread <= Fraction(1, 10),
            "counts": observed,
            "models": {m: predictions(v, target) for m, v in models.items()},
            "cpu": overhead(pairs),
        }
        total_pairs.extend(pairs)
    return {
        "cases": cases,
        "passes": {
            g: {
                m: sum(cases[n]["models"][m]["pass"] for n in study[g])
                for m in ("fast", "paired_baseline", "flat_changes", "history")
            }
            for g in ("anchors", "prospective")
        },
        "cpu": overhead(total_pairs),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    raw = gzip.decompress((DATA / "dataset.json.gz").read_bytes())
    result = {
        "dataset_sha256": hashlib.sha256(raw).hexdigest(),
        **score(json.loads(raw)),
    }
    output = DATA / "evaluation.json"
    if args.verify:
        if json.loads(output.read_bytes()) != result:
            raise ValueError("calculated predictors differ from archive")
    else:
        output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result["passes"]))
