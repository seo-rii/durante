"""Score Scrivener order forecasts against independent native CPU observations."""
from collections import defaultdict
from fractions import Fraction
from math import ceil
from statistics import median

from cpp20_locality.verify import require
from scrivener_cache.score import fraction_in_range, score_cases

METHODS = ("midpoint", "ordinary", "distance", "pages", "branch", "branch_pages")


def forecasts(low_ns, high_ns, low, high, case):
    require("native_ns" not in case, "unexpected native label in held-out forecast")
    require(low_ns > 0 and high_ns > 0 and low["work"] > 0, "invalid native or work anchor")

    def position(field):
        return fraction_in_range(low[field], high[field], case[field])

    pages = position("misses")
    branch = position("bcm")
    positions = dict(midpoint=Fraction(1, 2), distance=position("position_bits"),
                     pages=pages, branch=branch, branch_pages=(pages + branch) / 2)
    estimates = {k: ceil(low_ns + fraction * (high_ns - low_ns)) for k, fraction in positions.items()}
    estimates["ordinary"] = ceil(Fraction(low_ns * case["work"], low["work"]))
    return estimates


def summarize(plan, rows, predictions, methods=METHODS):
    limits = plan["fixed_limits_ms"]
    require(limits == list(range(30, 86, 5)), "unexpected fixed limits")
    heldout = [c["name"] for c in plan["cases"] if c["group"] == "holdout"]
    controls = [c["name"] for c in plan["cases"] if c["group"] == "control"]
    require(set(predictions) == set(heldout + controls), "unexpected set of forecasts")
    native_ns = defaultdict(list)
    costs = defaultdict(list)
    for row in rows:
        cost = row["cpu_us"]
        require(type(cost) is int and cost >= 0, "invalid CPU cost")
        costs[row["arm"]].append(cost)
        if row["arm"] == "native":
            native_ns[row["name"], row["program"]].append(1000 * cost)

    cases = {}
    scored = []
    for name in heldout + controls:
        programs = {}
        require(set(predictions[name]) == set(plan["binaries"]), "missing solver forecast")
        for program in plan["binaries"]:
            times = native_ns[name, program]
            require(len(times) == 9 and min(times) > 0, "expected nine positive held-out native observations")
            actual = median(times)
            estimates = predictions[name][program]
            require(set(estimates) == set(methods) and all(type(t) is int and t >= 0 for t in estimates.values()),
                    "invalid sealed forecast")
            errors = {k: Fraction(ns - actual, actual) for k, ns in estimates.items()}
            programs[program] = dict(native_ns=times, median_native_ns=actual, predictions_ns=estimates,
                                     signed_error_ppm={k: round(err * 1_000_000) for k, err in errors.items()},
                                     within_20pct={k: abs(err) <= Fraction(1, 5) for k, err in errors.items()})
            if name in heldout:
                scored.append(dict(native_ns=times, median_ns=actual, predictions=estimates))
        cases[name] = programs

    summary = score_cases(scored, limits, methods)
    for method, result in summary.items():
        # With nine observations, a strict majority crosses exactly when the median crosses.
        false_pass = sum(case["predictions"][method] <= ms * 1_000_000 < case["median_ns"]
                         for case in scored for ms in limits)
        result["majority_false_pass"] = false_pass
        result["majority_false_timeout"] = result["fixed_limits"] - result["majority_correct"] - false_pass

    profile_cost = {arm: dict(runs=len(values), total_cpu_us=sum(values), median_cpu_us=median(values))
                    for arm, values in costs.items()}
    return dict(cases=cases, summary=summary, profile_cost=profile_cost)
