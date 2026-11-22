"""Score the predeclared binary-search training cells, contrasts and transfer inputs."""

import argparse
from collections import defaultdict
from fractions import Fraction
import gzip
import hashlib
import json
from pathlib import Path
from statistics import median

from feasibility import limits
from model import HERE, PLAN, ROOT, baseline, fit, means, predict
from study import rows

RESULT = ROOT / "experiments/results/gce-search-order"


def ppm(ratio):
    return round(ratio * 1_000_000)


def cpu_cost(pairs):
    fast, sampled = map(sum, zip(*pairs))
    return {
        "pairs": len(pairs),
        "fast_ns": fast,
        "sampled_ns": sampled,
        "pooled_overhead_ppm": ppm(Fraction(sampled, fast) - 1),
        "median_pair_overhead_ppm": ppm(median(Fraction(y, x) for x, y in pairs) - 1),
    }


def errors(values, target):
    relative = [v / target - 1 for v in values]
    return {
        "estimate_ns": [round(v) for v in values],
        "median_ns": round(median(values)),
        "signed_errors_ppm": [ppm(e) for e in relative],
        "pass": all(abs(e) <= Fraction(1, 5) for e in relative),
    }


def score(data):
    registered = {}
    grouped = defaultdict(list)
    for row, case, _ in rows(data):
        name = case["workload_id"]
        grouped[name].append(row)
        registered[name] = case
    groups = {"training": sum(PLAN["training"].values(), []), **PLAN["holdouts"]}
    if set(grouped) != set(sum(groups.values(), [])) or any(
        len(rs) != 4 for rs in grouped.values()
    ):
        raise ValueError("expected the full registered corpus")
    calibration = {}
    for name, records in grouped.items():
        records.sort(key=lambda row: row["repetition"])
        native = [
            Fraction(r[phase]["cpu_time_ns"])
            for r in records
            for phase in ("native_before", "native_after")
        ]
        calibration[name] = (native, [r["sampled"] for r in records])
    parameters = fit(calibration)
    results, contrasts, all_pairs = {}, {}, []
    for name, (native, samples) in calibration.items():
        mode = "jcc" if name.startswith("jcc_") else "cmov"
        predictions = defaultdict(list)
        pairs = []
        oracle = registered[name]["oracle"]
        for row, obs in zip(grouped[name], samples, strict=True):
            measured = obs["sample"]
            if measured["suffix_histogram"] != oracle["sampled_suffix_histogram"]:
                raise ValueError(
                    f"sampled indices disagree with the independent input replay for {name}"
                )
            predictions["fast"].append(baseline(row["fast"]))
            for model, value in predict(obs, mode, parameters).items():
                predictions[model].append(value)
            pairs.append((row["fast"]["cpu_time_ns"], obs["cpu_time_ns"]))
        target = median(native)
        full = means(oracle["full_suffix_histogram"])
        sampled = means(samples[0]["sample"]["suffix_histogram"])
        spread = (max(native) - min(native)) / target
        results[name] = {
            "native_ns": list(map(int, native)),
            "native_median_ns": str(target),
            "duration_pass": min(native) >= 20_000_000,
            "spread_ppm": ppm(spread),
            "spread_pass": spread <= Fraction(1, 10),
            "visits": samples[0]["sample"]["visits"],
            "sampled_pairs": samples[0]["sample"]["pairs"],
            "full_means": {k: str(x) for k, x in full.items()},
            "sampled_means": {k: str(x) for k, x in sampled.items()},
            "models": {
                model: errors(values, target) for model, values in predictions.items()
            },
            "cpu": cpu_cost(pairs),
        }
        all_pairs.extend(pairs)
    for label, (one, two) in PLAN["contrasts"].items():
        a, b = (results[n] for n in (one, two))
        x, y = a["native_ns"], b["native_ns"]
        diffs = [
            (Fraction(x[i] + x[i + 1], 2) - Fraction(y[i] + y[i + 1], 2))
            for i in range(0, 8, 2)
        ]
        contrasts[label] = {
            "median_difference_ns": str(
                median(map(Fraction, x)) - median(map(Fraction, y))
            ),
            "endpoint_range_ns": [min(x) - max(y), max(x) - min(y)],
            "round_differences_ns": list(map(str, diffs)),
            "prediction_differences_ns": {
                k: v["median_ns"] - b["models"][k]["median_ns"]
                for k, v in a["models"].items()
            },
        }
    jcc, cmov = (contrasts[k + "_random_minus_sorted"] for k in ("jcc", "cmov"))
    return {
        "feasibility": {
            mode: {
                feature: limits(
                    calibration,
                    [n for n in calibration if n.startswith("jcc_") == (mode == "jcc")],
                    feature,
                )
                for feature in ("suffix", "above_9")
            }
            for mode in ("cmov", "jcc")
        },
        "parameters": {
            mode: {
                k: {"ns_per_query": str(a), "ns_per_level": str(b)}
                for k, (a, b) in models.items()
            }
            for mode, models in parameters.items()
        },
        "cases": results,
        "contrasts": contrasts,
        "interaction_ns": str(
            Fraction(jcc["median_difference_ns"])
            - Fraction(cmov["median_difference_ns"])
        ),
        "interaction_by_round_ns": [
            str(Fraction(a) - Fraction(b))
            for a, b in zip(
                jcc["round_differences_ns"], cmov["round_differences_ns"], strict=True
            )
        ],
        "passes": {
            g: {
                m: sum(results[n]["models"][m]["pass"] for n in names)
                for m in results[names[0]]["models"]
            }
            for g, names in groups.items()
        },
        "cpu": cpu_cost(all_pairs),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    raw = gzip.decompress((RESULT / "dataset.json.gz").read_bytes())
    result = {
        "dataset_sha256": hashlib.sha256(raw).hexdigest(),
        "plan_sha256": hashlib.sha256(
            (HERE / "evaluation-plan.json").read_bytes()
        ).hexdigest(),
        **score(json.loads(raw)),
    }
    output = RESULT / "evaluation.json"
    if args.verify:
        if json.loads(output.read_bytes()) != result:
            raise ValueError("published scores differ from the observations")
    else:
        output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result["passes"]))
