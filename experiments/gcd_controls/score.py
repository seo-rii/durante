"""Score the registered GCD contrasts and historical-residual hypotheses."""

import argparse
from collections import defaultdict
from fractions import Fraction
import gzip
import hashlib
import json
from pathlib import Path
from statistics import median

from calibrate import CALIBRATION, PLAN

HERE = Path(__file__).resolve().parent
RESULT = HERE.parent / "results/gce-gcd-controls"


def center(values):
    return median(map(Fraction, values))


def ppm(value):
    return round(value * 1_000_000)


def estimates(numbers, target, tolerance):
    errors = [n / target - 1 for n in numbers]
    return {
        "ns": [round(n) for n in numbers],
        "median_ns": round(center(numbers)),
        "errors_ppm": [ppm(e) for e in errors],
        "pass": all(abs(e) <= tolerance for e in errors),
    }


def oracle_counts(name, population):
    divisions = population["divisions"]
    if name.startswith("stream_"):
        return {
            "divisor_edge": divisions - 1 if name == "stream_divisor" else 0,
            "chain_exit": 0,
            "length_change": 0,
        }
    if name == "gcd" or name.startswith("loop_"):
        order = "grouped" if name.endswith("grouped") else "original"
        return {
            "divisor_edge": population["edges"],
            "chain_exit": population["chains"],
            "length_change": population[order + "_changes"],
        }
    return None


def cost(pairs):
    plain, marked = map(sum, zip(*pairs))
    return {
        "pairs": len(pairs),
        "fast_cpu_ns": plain,
        "trips_cpu_ns": marked,
        "pooled_ratio_ppm": ppm(Fraction(marked, plain)),
        "median_pair_ratio_ppm": ppm(center(Fraction(y, x) for x, y in pairs)),
        "pair_ratios_ppm": [ppm(Fraction(y, x)) for x, y in pairs],
    }


def predicted(fast_observation, weights):
    prod = fast_observation["production"]
    metric = prod["latency_bound"]
    if metric["version"] != 9001:
        raise ValueError("expected the experimental divider clock")
    return (
        weights["latency_cycles"] * metric["cycles_x16"] / 16
        + weights["memory_bytes"] * prod["memory_operand_bytes"]["bytes"]
    )


def score(data):
    from study import rows

    plan, calibration = (json.loads(f.read_bytes()) for f in (PLAN, CALIBRATION))
    if calibration["plan_sha256"] != hashlib.sha256(PLAN.read_bytes()).hexdigest():
        raise ValueError("unregistered residual hypotheses")
    weights = {
        k: Fraction(v) for k, v in calibration["baseline_coefficients_ns"].items()
    }
    rates = {k: Fraction(v) for k, v in calibration["rates_ns"].items()}
    population = calibration["population"]
    tol = Fraction(plan["tolerance_ppm"], 1_000_000)
    by_case = defaultdict(list)
    for row, case, _ in rows(data):
        by_case[case["workload_id"]].append(row)
    if set(by_case) != set(plan["cases"]) or any(len(r) != 4 for r in by_case.values()):
        raise ValueError(
            "expected all ten registered cases with four complete quartets each"
        )
    summaries, raw = {}, {}
    for name in plan["cases"]:
        records = sorted(by_case[name], key=lambda r: r["repetition"])
        native = [
            r[p]["cpu_time_ns"]
            for r in records
            for p in ("native_before", "native_after")
        ]
        target = center(native)
        by_round = [
            center([r[k]["cpu_time_ns"] for k in ("native_before", "native_after")])
            for r in records
        ]
        spread = Fraction(max(native) - min(native), target)
        models = {
            "fast": [predicted(r["fast"], weights) for r in records],
            "online_baseline": [predicted(r["trips"], weights) for r in records],
        }
        known = oracle_counts(name, population)
        if known is not None:
            for feature, count in known.items():
                models[feature] = [n + rates[feature] * count for n in models["fast"]]
        observed = []
        for r in records:
            sites = r["trips"]["trip"]["sites"]
            observed.append(
                {
                    "chain_exit": sum(s["completed"] for s in sites),
                    "length_change": sum(s["changes"] for s in sites),
                    "visits": sum(s["visits"] for s in sites),
                    "pending": sum(s["pending_length"] for s in sites),
                    "sites": len(sites),
                }
            )
        for feature in ("chain_exit", "length_change"):
            models["online_" + feature] = [
                n + rates[feature] * g[feature]
                for n, g in zip(models["online_baseline"], observed, strict=True)
            ]
        pairs = [(r["fast"]["cpu_time_ns"], r["trips"]["cpu_time_ns"]) for r in records]
        features = [
            (
                r["fast"]["production"]["instructions"],
                r["fast"]["production"]["latency_bound"]["cycles_x16"],
                r["fast"]["production"]["memory_operand_bytes"]["bytes"],
            )
            for r in records
        ]
        raw[name] = {
            "native": native,
            "target": target,
            "models": models,
            "pairs": pairs,
            "features": features,
            "round": by_round,
        }
        summaries[name] = {
            "native_ns": {
                "min": min(native),
                "median": str(target),
                "max": max(native),
            },
            "spread_ppm": ppm(spread),
            "duration_pass": min(native) >= plan["min_native_ns"],
            "spread_pass": spread <= Fraction(plan["max_spread_ppm"], 1_000_000),
            "observed_counts": observed,
            "oracle_counts": known,
            "instrumented_cpu": cost(pairs),
            "models": {model: estimates(v, target, tol) for model, v in models.items()},
        }
    contrasts = {}
    for key, (a, b) in plan["contrasts"].items():
        x, y = raw[a], raw[b]
        lower = min(x["native"]) - max(y["native"])
        upper = max(x["native"]) - min(y["native"])
        common = len(set(x["features"] + y["features"])) == 1
        slow, quick = (x, y) if x["target"] >= y["target"] else (y, x)
        ratio = max(Fraction(1), Fraction(min(slow["native"]), max(quick["native"])))
        contrasts[key] = {
            "cases": [a, b],
            "median_difference_ns": str(x["target"] - y["target"]),
            "native_ratio_ppm": ppm(x["target"] / y["target"]),
            "favorable_difference_range_ns": [lower, upper],
            "round_difference_ns": [
                str(v - u) for v, u in zip(x["round"], y["round"], strict=True)
            ],
            "identical_fast_features": common,
            "minimum_common_error_ppm": ppm((ratio - 1) / (ratio + 1))
            if common
            else None,
            "predicted_difference_ns": {
                m: round(center(x["models"][m]) - center(y["models"][m]))
                for m in x["models"]
            },
        }
    remainder, counted = (contrasts[k] for k in ("order_remainder", "order_counted"))
    interaction = {
        "median_difference_of_differences_ns": str(
            Fraction(remainder["median_difference_ns"])
            - Fraction(counted["median_difference_ns"])
        ),
        "by_round_ns": [
            str(Fraction(v) - Fraction(u))
            for v, u in zip(
                remainder["round_difference_ns"],
                counted["round_difference_ns"],
                strict=True,
            )
        ],
    }
    totals = {}
    for group, names in plan["groups"].items():
        totals[group] = {
            k: sum(summaries[n]["models"][k]["pass"] for n in names)
            for k in summaries[names[0]]["models"]
        }
    return {
        "cases": summaries,
        "contrasts": contrasts,
        "order_by_control_interaction": interaction,
        "passed": totals,
        "instrumented_cpu": {
            **{
                g: cost([p for n in members for p in raw[n]["pairs"]])
                for g, members in plan["groups"].items()
            },
            "all": cost([p for c in raw.values() for p in c["pairs"]]),
        },
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    raw = gzip.decompress((RESULT / "dataset.json.gz").read_bytes())
    result = {
        "dataset_sha256": hashlib.sha256(raw).hexdigest(),
        "calibration_sha256": hashlib.sha256(CALIBRATION.read_bytes()).hexdigest(),
        **score(json.loads(raw)),
    }
    output = RESULT / "evaluation.json"
    if args.verify:
        if json.loads(output.read_bytes()) != result:
            raise ValueError("recomputed scores differ")
    else:
        output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result["passed"]))
