"""Freeze three separate allocations of the previously observed Rust GCD residual."""

from collections import Counter
from fractions import Fraction
import argparse
import hashlib
import json
from math import gcd
from pathlib import Path
import runpy
from statistics import median

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PLAN = HERE / "evaluation-plan.json"
CALIBRATION = HERE / "calibration.json"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def original_population():
    holdout = ROOT / "experiments/rust_instruction_mix/holdout"
    fixture = json.loads((holdout / "fixtures.json").read_bytes())["programs"]["gcd"][
        "cases"
    ][0]
    pairs, seed = map(int, fixture["stdin"].split())
    words = runpy.run_path(str(holdout / "oracles.py"))["random_words"](seed)
    lengths = Counter()
    changes = divisions = checksum = 0
    previous = None
    for _ in range(pairs):
        a = max(1, next(words) & 0x7FFFFFFF)
        b = max(1, next(words) & 0x7FFFFFFF)
        expected = gcd(a, b)
        length = 0
        while b:
            a, b = b, a % b
            length += 1
        if a != expected:
            raise ValueError("GCD input oracle disagrees")
        checksum += a
        divisions += length
        changes += previous is not None and length != previous
        previous = length
        lengths[length] += 1
    if f"{checksum}\n" != fixture["stdout"]:
        raise ValueError("archived GCD checksum mismatch")
    return {
        "chains": pairs,
        "divisions": divisions,
        "edges": divisions - pairs,
        "checksum": checksum,
        "original_changes": changes,
        "grouped_changes": len(lengths) - 1,
        "length_histogram": {str(k): lengths[k] for k in sorted(lengths)},
    }


def calibration():
    plan = json.loads(PLAN.read_bytes())
    reference = ROOT / plan["reference"]
    case = json.loads(reference.read_bytes())["cases"]["gcd"]
    pop = original_population()
    if case["executed_main_div32"] != [pop["divisions"]] * 3:
        raise ValueError("archived measured DIV count mismatch")
    native = Fraction(case["native_median_ns"])
    estimate = median(case["fast"]["estimates_ns"])
    residual = native - estimate
    rates = {
        name: str(residual / pop[key])
        for name, key in (
            ("divisor_edge", "edges"),
            ("chain_exit", "chains"),
            ("length_change", "original_changes"),
        )
    }
    history = ROOT / "experiments/divide_clock/protocol.json"
    weights_path = ROOT / json.loads(history.read_bytes())["reference"]
    weights = json.loads(weights_path.read_bytes())["models_on_medians"][
        "latency_memory"
    ]["coefficients_ns"]
    return {
        "schema": 1,
        "reference_sha256": sha(reference),
        "plan_sha256": sha(PLAN),
        "historical_native_ns": str(native),
        "historical_fast_ns": estimate,
        "residual_ns": str(residual),
        "population": pop,
        "rates_ns": rates,
        "baseline_coefficients_ns": weights,
        "baseline_reference_sha256": sha(weights_path),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--freeze", action="store_true", help="write the calibration once"
    )
    args = parser.parse_args()
    result = calibration()
    if args.freeze:
        with CALIBRATION.open("x") as f:
            f.write(json.dumps(result, indent=2) + "\n")
    elif result != json.loads(CALIBRATION.read_bytes()):
        raise ValueError(
            "frozen calibration differs from the archive and original input"
        )
    print(
        "GCD residual (ns)",
        result["residual_ns"],
        "Rates (ns per event)",
        result["rates_ns"],
    )
