"""Forecast the cost of query streams and compare paired timing and deadline errors."""
from math import ceil

from scrivener_cache.score import fraction_in_range
from scrivener_pages import score as pages

METHODS = (*pages.METHODS, "stream", "branch_stream")


def forecasts(low_ns, high_ns, low, high, case):
    estimates = pages.forecasts(low_ns, high_ns, low, high, case)
    stream = fraction_in_range(low["disruptive"], high["disruptive"], case["disruptive"])
    branch = fraction_in_range(low["bcm"], high["bcm"], case["bcm"])
    estimates["stream"] = ceil(low_ns + stream * (high_ns - low_ns))
    estimates["branch_stream"] = ceil(low_ns + (branch + stream) / 2 * (high_ns - low_ns))
    return estimates


def summarize(plan, rows, predictions):
    return pages.summarize(plan, rows, predictions, methods=METHODS)


def qualification(cases, limits):
    from scrivener_pages.verify import qualification as compare

    comparisons = {}
    for baseline, candidate in (("branch_pages", "branch_stream"), ("pages", "stream")):
        comparison = compare(cases, limits, baseline=baseline, candidate=candidate)
        changes = comparison["unanimous_error_changes"]
        comparison["deadline_gate"] = (not changes["false_pass_added"] and not changes["false_timeout_added"]
                                       and bool(changes["false_timeout_fixed"] or changes["false_pass_fixed"]))
        comparisons[candidate] = comparison
    return comparisons
