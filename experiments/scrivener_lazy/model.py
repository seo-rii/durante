"""Decide a branch/stream clock deadline using exact bounds before requesting counters."""
from fractions import Fraction
from math import ceil

from scrivener_cache.score import fraction_in_range


def _fraction_bounds(low, high, observed):
    if low == high:
        return Fraction(1, 2), Fraction(1, 2)
    if observed is None:
        return Fraction(0), Fraction(1)
    value = fraction_in_range(low, high, observed)
    return value, value


def _time_bounds(low, high, stream, branch):
    s_min, s_max = _fraction_bounds(low["disruptive"], high["disruptive"], stream)
    b_min, b_max = _fraction_bounds(low["bcm"], high["bcm"], branch)
    base = low["native_ns"]
    span = high["native_ns"] - base
    a = ceil(base + span * (s_min + b_min) / 2)
    b = ceil(base + span * (s_max + b_max) / 2)
    return min(a, b), max(a, b)


def _count(value):
    if type(value) is not int or value < 0:
        raise ValueError("counter must be a nonnegative integer")
    return value


def decide(anchors, limit_ns, scan, branch, *, eager=False):
    """Return an inclusive estimate interval and its strict-greater-than deadline decision.

    The scan and branch callables measure the submission only when invoked.
    Eager mode always requests both, giving the exact #106 estimate.
    """
    if type(limit_ns) is not int or limit_ns < 0:
        raise ValueError("budget must be a nonnegative integer in nanoseconds")
    low, high = anchors["ascending"], anchors["shuffled"]
    for anchor in (low, high):
        if type(anchor["native_ns"]) is not int or anchor["native_ns"] <= 0:
            raise ValueError("native anchor time must be a positive integer in nanoseconds")
        _count(anchor["disruptive"])
        _count(anchor["bcm"])

    lower, upper = _time_bounds(low, high, None, None)
    path = "anchors"
    if eager or lower <= limit_ns < upper:
        stream = _count(scan())
        lower, upper = _time_bounds(low, high, stream, None)
        path = "scan"
        if eager or lower <= limit_ns < upper:
            lower, upper = _time_bounds(low, high, stream, _count(branch()))
            path = "branch"
    return dict(path=path, over_budget=lower > limit_ns, lower_ns=lower, upper_ns=upper)
