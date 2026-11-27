"""Forecast Scrivener CPU time from the age and local distance of disruptive page revisits."""
from fractions import Fraction
from math import ceil
from statistics import median

from scrivener_cache.score import fraction_in_range
from scrivener_pages import score as pages
from scrivener_streams import score as streams

FILTERS = {"branch_stale": "stale_disruptive", "branch_displacement": "displacement"}
CANDIDATES = ("midpoint_adjusted", *FILTERS)
METHODS = (*streams.METHODS, *CANDIDATES)


def forecasts(low_ns, high_ns, low, high, case):
    estimates = streams.forecasts(low_ns, high_ns, low, high, case)
    estimates["midpoint_adjusted"] = (estimates["midpoint"] + estimates["branch_stream"] + 1) // 2
    branch = fraction_in_range(low["bcm"], high["bcm"], case["bcm"])
    disruptive = fraction_in_range(low["disruptive"], high["disruptive"], case["disruptive"])
    for method, field in FILTERS.items():
        stream = disruptive
        if low[field] != high[field]:
            stream = min(stream, fraction_in_range(low[field], high[field], case[field]))
        estimates[method] = ceil(low_ns + (branch + stream) / 2 * (high_ns - low_ns))
    return estimates


def summarize(plan, rows, predictions):
    return pages.summarize(plan, rows, predictions, methods=METHODS)


def qualification(cases, limits):
    """Compare held-out timing errors and individual consensus deadlines with the original stream clock."""
    from scrivener_pages.verify import qualification as compare

    baseline = "branch_stream"
    median_error = {}
    for method in (baseline, *CANDIDATES):
        median_error[method] = median(abs(Fraction(case["predictions_ns"][method], case["median_native_ns"]) - 1)
                                      for programs in cases.values() for case in programs.values())

    comparisons = {}
    for method in CANDIDATES:
        result = compare(cases, limits, baseline=baseline, candidate=method)
        changes = result["unanimous_error_changes"]
        result["worst_error_improved"] = result["accuracy_gate"]
        result["median_error_nonincreasing"] = median_error[method] <= median_error[baseline]
        result["accuracy_gate"] = result["worst_error_improved"] and result["median_error_nonincreasing"]
        result["deadline_gate"] = (not changes["false_pass_added"] and not changes["false_timeout_added"]
                                   and bool(changes["false_pass_fixed"] or changes["false_timeout_fixed"]))
        comparisons[method] = result
    return comparisons
