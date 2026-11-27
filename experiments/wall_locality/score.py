"""Combine separately normalized branch and locality features of the same executable."""
from fractions import Fraction
from math import ceil
from statistics import median

from cpp20_locality.verify import require
from guarded_native.anchor import weights
from guarded_native.transfer import scaled_ns, work
from scrivener_cache.score import fraction_in_range

METHODS = ("ordinary", "midpoint", "branch_misses", "ll_read_misses", "pages_only", "branch_pages", "full_joint")


def predict(ascending, shuffled, holdout):
    require("native_ns" not in holdout, "unexpected native label in held-out forecast")
    a, b = (median(c["native_ns"]) for c in (ascending, shuffled))

    def fraction(arm, event):
        return fraction_in_range(*(c[arm][event] for c in (ascending, shuffled, holdout)))

    bcm = fraction("branch_only", "Bcm")
    ll = fraction("full", "DLmr")
    pages = fraction("leaf_pages", "misses")
    positions = dict(midpoint=Fraction(1, 2), branch_misses=bcm, ll_read_misses=ll,
                     pages_only=pages, branch_pages=max(bcm, pages),
                     full_joint=max(fraction("full", "Bcm"), ll))
    coeffs = {k: Fraction(v) for k, v in weights().items()}
    estimates = {"ordinary": scaled_ns(a, work(holdout["ordinary"], coeffs), work(ascending["ordinary"], coeffs))}
    estimates.update({name: ceil(a + pos * (b - a)) for name, pos in positions.items()})
    return estimates
