"""Forecast Wall CPU time before observing the held-out native runs."""
from fractions import Fraction
from math import ceil
from statistics import median

from cpp20_locality.verify import require
from guarded_native.anchor import weights
from guarded_native.transfer import scaled_ns, work
from scrivener_cache.score import fraction_in_range

# Rounded median of the four native ns / simulated Bcm slopes from Scrivener #99.
TRANSFER_NS_PER_MISS = 146
METHODS = ("ordinary", "midpoint", "branch_misses", "ll_read_misses", "transferred_branch")


def predict(ascending, shuffled, holdout):
    require("native_ns" not in holdout, "unexpected native label in held-out forecast")
    a, b = (median(row["native_ns"]) for row in (ascending, shuffled))
    coefficients = {key: Fraction(value) for key, value in weights().items()}
    ordinary = scaled_ns(a, work(holdout["ordinary"], coefficients),
                         work(ascending["ordinary"], coefficients))
    forecasts = dict(ordinary=ordinary, midpoint=ceil(Fraction(a + b, 2)))
    for method, arm, event in (("branch_misses", "branch_only", "Bcm"), ("ll_read_misses", "full", "DLmr")):
        values = (case[arm][event] for case in (ascending, shuffled, holdout))
        forecasts[method] = ceil(a + fraction_in_range(*values) * (b - a))
    extra_misses = holdout["branch_only"]["Bcm"] - ascending["branch_only"]["Bcm"]
    forecasts["transferred_branch"] = max(1, a + TRANSFER_NS_PER_MISS * extra_misses)
    return forecasts
