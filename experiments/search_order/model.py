"""Fit and apply the registered query-order corrections to a version-9001 clock."""

from fractions import Fraction
import json
from pathlib import Path
from statistics import median

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PLAN = json.loads((HERE / "evaluation-plan.json").read_bytes())
WEIGHTS = {
    k: Fraction(v)
    for k, v in json.loads((ROOT / PLAN["baseline"]).read_bytes())[
        "baseline_coefficients_ns"
    ].items()
}


def baseline(observation):
    counters = observation["production"]
    latency = counters["latency_bound"]
    if latency["version"] != 9001:
        raise ValueError("the search-order model requires the v9001 divider clock")
    return (
        WEIGHTS["latency_cycles"] * latency["cycles_x16"] / 16
        + WEIGHTS["memory_bytes"] * counters["memory_operand_bytes"]["bytes"]
    )


def means(histogram):
    if len(histogram) != 61 or any(type(n) is not int or n < 0 for n in histogram):
        raise ValueError("invalid terminal-index width histogram")
    total = sum(histogram)
    if not total:
        raise ValueError("at least one complete pair is required")
    return {
        name: Fraction(sum(max(i - cut, 0) * n for i, n in enumerate(histogram)), total)
        for name, cut in (("suffix", 0), ("above_9", PLAN["hot_line_bits"]))
    }


def query_features(sample):
    visits = sample["visits"]
    if sample["pairs"] != sum(sample["suffix_histogram"]) or visits < 2:
        raise ValueError("inconsistent terminal comparison count")
    return {
        k: v * (visits - 1) / visits
        for k, v in means(sample["suffix_histogram"]).items()
    }


def fit(cases):
    fitted = {}
    for mode, names in PLAN["training"].items():
        points = []
        for name in names:
            native, observations = cases[name]
            residual = median(native) - median(baseline(obs) for obs in observations)
            visits = {obs["sample"]["visits"] for obs in observations}
            features = [query_features(obs["sample"]) for obs in observations]
            if len(visits) != 1 or any(f != features[0] for f in features):
                raise ValueError("inconsistent repeats in registered calibration")
            points.append((features[0], residual / visits.pop()))
        (low, a), (high, b) = points
        rates = {"query_only": ((a + b) / 2, Fraction(0))}
        for feature in low:
            if high[feature] <= low[feature]:
                raise ValueError(
                    "the two controls do not identify the registered coefficient"
                )
            slope = (b - a) / (high[feature] - low[feature])
            rates[feature] = (a - slope * low[feature], slope)
        fitted[mode] = rates
    return fitted


def predict(observation, mode, parameters):
    base = baseline(observation)
    sample = observation["sample"]
    features = {"query_only": Fraction(0), **query_features(sample)}
    return {
        "paired_baseline": base,
        **{
            key: base + sample["visits"] * (intercept + slope * features[key])
            for key, (intercept, slope) in parameters[mode].items()
        },
    }
