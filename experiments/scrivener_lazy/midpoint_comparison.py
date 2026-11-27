"""Post-hoc comparison of a midpoint-adjusted clock across two Scrivener campaigns."""
from fractions import Fraction
import json
from pathlib import Path
from statistics import median
import sys

EXPERIMENTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EXPERIMENTS))
from scrivener_streams.score import forecasts


def load_study(study):
    plan = json.loads((EXPERIMENTS / ("scrivener_" + study) / "plan.json").read_bytes())
    boots = []
    for suffix in ("", "-repeat"):
        path = EXPERIMENTS / "results" / ("gce-scrivener-" + study + suffix) / "metrics.json"
        boots.append(json.loads(path.read_bytes()))
    return plan, boots


def held_out(plan, report, study):
    observations = []
    for case in plan["cases"]:
        if case["group"] != "holdout":
            continue
        for pair in report["cases"][case["name"]].values():
            if study == "streams":
                midpoint, stream = (pair["predictions_ns"][key] for key in ("midpoint", "branch_stream"))
            else:
                midpoint, stream = pair["midpoint_ns"], pair["eager_estimate_ns"][0]
            observations.append((midpoint, stream, pair["median_native_ns"], pair["native_ns"]))
    return observations


def prediction(midpoint, stream, k):
    # ceil(midpoint + k/100 * (stream - midpoint)), including when stream < midpoint.
    return ((100 - k) * midpoint + k * stream + 99) // 100


def errors(observations, k):
    return [abs(Fraction(prediction(midpoint, stream, k) - actual, actual))
            for midpoint, stream, actual, _ in observations]


def ppm(rate):
    return round(rate * 1_000_000)


def summarize(observations, k, limits):
    rates = errors(observations, k)
    grid = {key: 0 for key in ("majority_correct", "majority_false_pass", "majority_false_timeout",
                               "unanimous_correct", "unanimous_false_pass", "unanimous_false_timeout", "mixed")}
    for midpoint, stream, _, native in observations:
        estimate = prediction(midpoint, stream, k)
        for ms in limits:
            limit = ms * 1_000_000
            over = estimate > limit
            votes = sum(t > limit for t in native)
            majority_over = 2 * votes > len(native)
            result = "correct" if over == majority_over else "false_timeout" if over else "false_pass"
            grid["majority_" + result] += 1
            grid["mixed" if 0 < votes < len(native) else "unanimous_" + result] += 1
    return dict(cases=len(rates), within_20pct=sum(r <= Fraction(1, 5) for r in rates),
                median_absolute_error_ppm=ppm(median(rates)), worst_absolute_error_ppm=ppm(max(rates)), grid=grid)


def grid_changes(observations, k, limits):
    changes = {rule: {key: 0 for key in ("false_pass_added", "false_pass_fixed", "false_timeout_added", "false_timeout_fixed")}
               for rule in ("majority", "unanimous")}
    for midpoint, stream, center, native in observations:
        selected = prediction(midpoint, stream, k)
        for ms in limits:
            limit = ms * 1_000_000
            before, after = stream > limit, selected > limit
            if before == after:
                continue
            native_tle = center > limit
            error = "false_pass" if native_tle else "false_timeout"
            key = error + ("_fixed" if after == native_tle else "_added")
            changes["majority"][key] += 1
            if not (min(native) <= limit < max(native)):
                changes["unanimous"][key] += 1
    return changes


def prior_anchor_control(boot, weight):
    results = {}
    for program in boot["cases"]["ascending"]:
        low, high = (boot["cases"][name][program] for name in ("ascending", "shuffled"))
        for name, case in (("ascending", low), ("shuffled", high), ("lanes32", boot["controls"]["lanes32"][program])):
            previous = forecasts(low["median_native_ns"], high["median_native_ns"],
                                 low["features"], high["features"], case["features"])
            midpoint, stream = previous["midpoint"], previous["branch_stream"]
            results.setdefault(name, {})[program] = dict(native_ns=case["median_native_ns"], midpoint_ns=midpoint,
                                                         branch_stream_ns=stream, selected_ns=prediction(midpoint, stream, weight))
    return dict(boot_sha256=boot["boot_sha256"], cases=results)


def compare():
    prior_plan, prior_boots = load_study("streams")
    training = [row for boot in prior_boots for row in held_out(prior_plan, boot, "streams")]

    def objective(k):
        rates = errors(training, k)
        return max(rates), median(rates), k

    worst, middle, weight = min(objective(k) for k in range(101))
    evaluation_plan, evaluation_boots = load_study("lazy")
    limits = evaluation_plan["fixed_limits_ms"]
    evaluation = []
    for boot in evaluation_boots:
        pairs = held_out(evaluation_plan, boot, "lazy")
        methods = {method: summarize(pairs, k, limits)
                   for method, k in (("branch_stream", 100), ("selected", weight), ("midpoint", 0))}
        evaluation.append(dict(boot_sha256=boot["boot_sha256"], methods=methods,
                               selected_vs_branch_stream=grid_changes(pairs, weight, limits)))
    return dict(authority="post_hoc_diagnostic_only", selected_weight_percent=weight,
                training=dict(study="scrivener_streams", observations=len(training),
                              boot_sha256=[b["boot_sha256"] for b in prior_boots],
                              median_absolute_error_ppm=ppm(middle), worst_absolute_error_ppm=ppm(worst)),
                prior_anchor_control=[prior_anchor_control(b, weight) for b in prior_boots], evaluation=evaluation)


if __name__ == "__main__":
    print(json.dumps(compare(), indent=2))
