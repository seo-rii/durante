"""Compare the registered live clocks and two descriptive completed-input projections on verified receipts."""
import argparse
from collections import Counter
from fractions import Fraction
import json
from pathlib import Path
from statistics import median
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from cpp20_locality.verify import require
from deadline_accuracy.verify import verify
from guarded_native.score import MS

LIVE = ("baseline", "warm")
OFFLINE = ("first_postwarm", "median_first_three")


def median_index(samples, indices):
    # Sort stability preserves chronological order for equal CPU observations.
    return sorted(indices, key=samples.__getitem__)[1]


def case_record(trial):
    samples = [trial["baseline_ns"], *trial["extra_ns"]]
    require(len(samples) == 4 and trial["baseline_paid_cpu_ns"] == samples[0],
            "expected one current payment followed by three new payments")
    indices = dict(baseline=0, warm=median_index(samples, (1, 2, 3)), first_postwarm=1,
                   median_first_three=median_index(samples, (0, 1, 2)))
    require(samples[indices["warm"]] == trial["packet_warm_ns"], "warm median selection differs")
    points = []
    for event in trial["deadlines"]:
        limit = event["limit_ms"]
        # At the completed calibration capture, ceil(work * cpu_ns / anchor_work)
        # equals this observation's integer cpu_ns. These two projections have no live run.
        inferred = {model: samples[indices[model]] > limit * MS for model in OFFLINE}
        points.append(dict(limit_ms=limit, fixed=event["fixed"], adaptive=bool(event["adaptive_percent"]),
                           native_tle=event["native_tle_reps"], actual_tle={k:event[k]["tle"] for k in LIVE},
                           model_implied_tle=inferred))
    return dict(program=trial["program"], cohort=trial["cohort"], profile_cpu_ns=trial["profile_cpu_ns"],
                observations_ns=samples, selected_indices=indices, native_ns=trial["native_ns"], deadlines=points)


def error_ns(case, method):
    return case["observations_ns"][case["selected_indices"][method]] - median(case["native_ns"])


def duration(cases, method):
    deltas = [error_ns(c, method) for c in cases]
    relative = [Fraction(d, median(c["native_ns"])) for c, d in zip(cases, deltas, strict=True)]
    result = dict(median_absolute_error_ppm=round(median(abs(x) for x in relative) * MS),
                  maximum_absolute_error_ppm=round(max(map(abs, relative)) * MS),
                  median_signed_error_ppm=round(median(relative) * MS),
                  within_20pct=sum(abs(x) * 5 <= 1 for x in relative))
    for reference in LIVE:
        if method != reference:
            changes = [abs(error_ns(c, reference)) - abs(d) for c, d in zip(cases, deltas, strict=True)]
            result["compared_with_" + reference] = dict(wins=sum(x > 0 for x in changes),
                                                       ties=changes.count(0), losses=sum(x < 0 for x in changes))
    return result


def deadline_tally(points, verdict_key, method):
    tally = Counter()
    for point, n in points:
        tle = point[verdict_key][method]
        votes = point["native_tle"]
        if votes in (0, n):
            if tle == (votes == n):
                tally["unanimous_correct"] += 1
            elif tle:
                tally["false_tle"] += 1
            else:
                tally["false_accept"] += 1
        tally["majority_correct"] += tle == (2 * votes > n)
    return {name:tally[name] for name in ("unanimous_correct", "false_tle", "false_accept", "majority_correct")}


def score_limits(points):
    return dict(limits=len(points), mixed=sum(0 < p["native_tle"] < n for p, n in points),
                native_decisions=sum(n for _, n in points),
                minimum_disagreements_with_repetitions=sum(min(p["native_tle"], n-p["native_tle"]) for p,n in points),
                actual={method:deadline_tally(points, "actual_tle", method) for method in LIVE},
                model_implied={method:deadline_tally(points, "model_implied_tle", method) for method in OFFLINE})


def summarize(cases):
    points = [(p, len(c["native_ns"])) for c in cases for p in c["deadlines"]]
    absolute = [p for p in points if p[0]["fixed"]]
    adaptive = [p for p in points if p[0]["adaptive"]]
    return dict(inputs=len(cases), profile_cpu_ns=sum(c["profile_cpu_ns"] for c in cases),
                first_native_cpu_ns=sum(c["observations_ns"][0] for c in cases),
                extra_native_cpu_ns=sum(sum(c["observations_ns"][1:]) for c in cases),
                extra_native_runs=sum(len(c["observations_ns"]) - 1 for c in cases),
                independent_native_cpu_ns=sum(sum(c["native_ns"]) for c in cases),
                actual_duration={method:duration(cases, method) for method in LIVE},
                model_implied_duration={method:duration(cases, method) for method in OFFLINE},
                all=score_limits(points), fixed=score_limits(absolute), adaptive=score_limits(adaptive),
                fixed_adaptive_overlap=len(absolute) + len(adaptive) - len(points))


def analyze(directory):
    m = verify(directory, "warm-three")
    cases = {name:case_record(trial) for name,trial in m["cases"].items()}
    cohorts = {group:summarize([c for c in cases.values() if c["cohort"] == group])
               for group in sorted({c["cohort"] for c in cases.values()})}
    families = {program:summarize([c for c in cases.values() if c["program"] == program])
                for program in sorted({c["program"] for c in cases.values()})}
    return dict(schema=1, authority="diagnostic_only", summary_sha256=m["summary_sha256"],
                plan_sha256=m["plan_sha256"], rules_sha256=m["rules_sha256"],
                primary="prospective", cohorts=cohorts, programs=families, all=summarize(list(cases.values())),
                cases=cases)


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("directory", type=Path)
    cli.add_argument("--output", type=Path)
    args = cli.parse_args()
    report = analyze(args.directory)
    if args.output:
        args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report["cohorts"], indent=2))
