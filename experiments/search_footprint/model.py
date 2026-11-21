"""Evaluate a calibrated active-index-span correction using only the measured process."""

from bisect import bisect_left
from fractions import Fraction
import json
from pathlib import Path
from statistics import median

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PLAN = json.loads((HERE / "evaluation-plan.json").read_bytes())
WEIGHTS = json.loads((ROOT / PLAN["baseline"]).read_bytes())["baseline_coefficients_ns"]
PREVIOUS = json.loads((ROOT / PLAN["previous"]).read_bytes())["parameters"]["cmov"][
    "suffix"
]
DATA = ROOT / "experiments/results/gce-search-footprint"


def baseline(obs):
    prod = obs["production"]
    latency = prod["latency_bound"]
    if latency["version"] != 9001:
        raise ValueError("expected version-9001 diagnostic")
    return (
        Fraction(WEIGHTS["latency_cycles"]) * latency["cycles_x16"] / 16
        + Fraction(WEIGHTS["memory_bytes"]) * prod["memory_operand_bytes"]["bytes"]
    )


def previous_estimate(obs):
    s = obs["sample"]
    width = Fraction(s["suffix_sum"], s["pairs"])
    return (
        baseline(obs)
        + Fraction(PREVIOUS["ns_per_query"]) * s["visits"]
        + Fraction(PREVIOUS["ns_per_level"]) * width * (s["visits"] - 1)
    )


def span(s):
    return 4 * (s["sampled_max_index"] - s["sampled_min_index"] + 1)


def uniform_distance(s):
    """Compare P(width <= k), accounting for the two partial edge regions."""
    lo, hi = s["sampled_min_index"], s["sampled_max_index"]
    count = hi - lo + 1
    cumulative = 0
    distance = Fraction(0)
    for width, bucket in enumerate(s["suffix_histogram"]):
        cumulative += bucket
        block = 1 << (4 + width)
        first, last = lo // block, hi // block
        if first == last:
            expected = Fraction(1)
        else:
            left, right = block - lo % block, hi % block + 1
            squared = left * left + right * right + (last - first - 1) * block * block
            expected = Fraction(squared, count * count)
        distance = max(distance, abs(Fraction(cumulative, s["pairs"]) - expected))
    return distance


def calibrate(records, names):
    knots = []
    identity = set()
    for name in names:
        case, rows = records[name]
        identity.add((case["binary_sha256"], case["terminal_offset"]))
        native = [
            Fraction(r[k]["cpu_time_ns"])
            for r in rows
            for k in ("native_before", "native_after")
        ]
        sampled = [r["sampled"] for r in rows]
        features = {(span(o["sample"]), o["sample"]["visits"]) for o in sampled}
        if len(features) != 1 or any(
            uniform_distance(o["sample"]) > Fraction(1, 12) for o in sampled
        ):
            raise ValueError(
                "calibration activity does not match the registered distribution"
            )
        size, visits = features.pop()
        residual = (median(native) - median(baseline(o) for o in sampled)) / visits
        knots.append({"case": name, "span_bytes": size, "ns_per_query": str(residual)})
    if (
        len(identity) != 1
        or len(knots) < 2
        or len({p["span_bytes"] for p in knots}) != len(knots)
    ):
        raise ValueError(
            "calibration requires one executable and distinct observed spans"
        )
    binary, pc = identity.pop()
    return {
        "schema": 1,
        "binary_sha256": binary,
        "terminal_offset": pc,
        "knots": sorted(knots, key=lambda p: p["span_bytes"]),
    }


def estimate(obs, binary_sha256, model):
    s = obs["sample"]
    if binary_sha256 != model["binary_sha256"] or s["pc"] != model["terminal_offset"]:
        return None, "unregistered_executable_or_site"
    if s["pairs"] < 256:
        return None, "too_few_pairs"
    if uniform_distance(s) > Fraction(1, 12):
        return None, "endpoint_distribution"
    x = span(s)
    knots = model["knots"]
    xs = [p["span_bytes"] for p in knots]
    if 100 * x < 95 * xs[0] or 100 * x > 105 * xs[-1]:
        return None, "outside_calibrated_span"
    right = min(max(bisect_left(xs, x), 1), len(xs) - 1)
    x0, x1 = xs[right - 1 : right + 1]
    y0, y1 = (Fraction(k["ns_per_query"]) for k in knots[right - 1 : right + 1])
    weight = min(Fraction(1), max(Fraction(0), Fraction(x - x0, x1 - x0)))
    return baseline(obs) + s["visits"] * (y0 + weight * (y1 - y0)), "eligible"
