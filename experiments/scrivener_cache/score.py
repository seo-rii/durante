"""Estimate CPU time from two paid endpoint orders of the same executable and guest."""
from fractions import Fraction
from math import ceil
from statistics import median

from cpp20_locality.verify import require
from guarded_native.anchor import weights
from guarded_native.transfer import scaled_ns, work

METHODS = ("ordinary", "midpoint", "distance", "ll_read_misses", "branch_misses")
FEATURES = {"ll_read_misses": "DLmr", "branch_misses": "Bcm"}


def fraction_in_range(low, high, current):
    if high == low:
        return Fraction(1, 2)
    return max(Fraction(0), min(Fraction(1), Fraction(current - low, high - low)))


def predict(ascending, shuffled, holdout):
    """No held-out native measurement is available to the forecasting step."""
    require("native_ns" not in holdout, "unexpected native label in held-out forecast")
    a, b = (median(row["native_ns"]) for row in (ascending, shuffled))
    coefficients = {k: Fraction(value) for k, value in weights().items()}
    forecasts = dict(ordinary=scaled_ns(a, work(holdout["ordinary"], coefficients),
                                       work(ascending["ordinary"], coefficients)),
                     midpoint=ceil(Fraction(a + b, 2)))
    for method in ("distance", *FEATURES):
        values = [row["position_bits"] if method == "distance" else row["cachegrind"][FEATURES[method]]
                  for row in (ascending, shuffled, holdout)]
        forecasts[method] = ceil(a + fraction_in_range(*values) * (b - a))
    return forecasts


def score_cases(cases, limits):
    results = {}
    for method in METHODS:
        errors = []
        correct = unanimous = false_timeout = false_pass = mixed = 0
        for case in cases:
            timings = case["native_ns"]
            prediction = case["predictions"][method]
            actual = median(timings)
            errors.append(abs(Fraction(prediction - actual, actual)))
            for ms in limits:
                threshold = ms * 1_000_000
                over = sum(t > threshold for t in timings)
                model_tle = prediction > threshold
                match = model_tle == (2 * over > len(timings))
                correct += match
                if 0 < over < len(timings):
                    mixed += 1
                else:
                    unanimous += match
                    false_timeout += model_tle and not match
                    false_pass += not model_tle and not match
        results[method] = dict(cases=len(cases), median_absolute_error_ppm=round(1_000_000 * median(errors)),
                               worst_absolute_error_ppm=round(1_000_000 * max(errors)),
                               within_20pct=sum(e <= Fraction(1, 5) for e in errors),
                               fixed_limits=len(cases) * len(limits), majority_correct=correct,
                               mixed_limits=mixed, unanimous_correct=unanimous,
                               unanimous_false_timeout=false_timeout, unanimous_false_pass=false_pass)
    return results
