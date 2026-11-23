"""Exploratory model-only comparison of the first and median native samples for near-limit requests."""
import argparse
from collections import Counter
import json
from pathlib import Path
from statistics import median
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from cpp20_locality.verify import require
from deadline_accuracy import gate, verify
from guarded_native.score import MS, boundary
from shape_calibration.verify import score_prediction

MODELS = ("baseline", "first", "median")


def score_case(trial, packet_samples, packet_source_reused):
    refs = trial["native_ns"]
    base = trial["baseline_ns"]
    has_near = any(p["near"] for p in trial["deadlines"])
    require(len(packet_samples) == (3 if has_near else 0), "expected one packet for the near deadlines")
    estimates = {"baseline": base}
    if has_near:
        estimates.update(first=packet_samples[0], median=int(median(packet_samples)))
        require(estimates["median"] == trial["packet_median_ns"], "different verified median")
    errors = {}
    for model, estimate in estimates.items():
        score = score_prediction(estimate, refs, [])
        errors[model] = {k: score[k] for k in ("prediction_ns", "within_20pct", "signed_error_ppm")}
    decisions = []
    for point in trial["deadlines"]:
        ms = point["limit_ms"]
        eligible = gate.near(base, ms)
        require(eligible == point["near"], "different cutoff eligibility")
        verdicts = {}
        for model in MODELS:
            estimate = estimates[model] if eligible else base
            comparison = boundary(refs, [estimate], ms)
            verdicts[model] = dict(tle=bool(comparison["model_tle_reps"]),
                                   native_class=comparison["classification"])
        decisions.append(dict(limit_ms=ms, near=eligible, native_tle_reps=point["native_tle_reps"],
                              native_equal_reps=sum(t == ms * MS for t in refs), verdicts=verdicts))
    return dict(cohort=trial["cohort"], source="current" if trial["baseline_paid_cpu_ns"] else "transferred",
                reused_original_observation=packet_source_reused, packet_samples_ns=packet_samples,
                reference_ns=refs, reference_range_ns=[min(refs), max(refs)],
                duration=errors, deadlines=decisions)


def summarize(cases):
    total = dict(cases=len(cases), packets=sum(bool(c["packet_samples_ns"]) for c in cases),
                 duration={}, near_decisions={}, far_decisions={})
    for model in MODELS:
        scored = [c["duration"][model] for c in cases if model in c["duration"]]
        absolute = [abs(c["signed_error_ppm"]) for c in scored]
        total["duration"][model] = dict(cases=len(scored), within_20pct=sum(c["within_20pct"] for c in scored),
                                        median_abs_ppm=median(absolute) if absolute else None,
                                        max_abs_ppm=max(absolute, default=None))
    for eligible, category in ((True, "near_decisions"), (False, "far_decisions")):
        points = [(d, len(c["reference_ns"])) for c in cases for d in c["deadlines"] if d["near"] == eligible]
        tally = total[category]
        tally.update(limits=len(points), exact_native_ties=sum(d["native_equal_reps"] for d, _ in points),
                     changed_baseline_to_first=sum(d["verdicts"]["baseline"]["tle"] != d["verdicts"]["first"]["tle"]
                                                   for d, _ in points),
                     changed_first_to_median=sum(d["verdicts"]["first"]["tle"] != d["verdicts"]["median"]["tle"]
                                                 for d, _ in points))
        for model in MODELS:
            classes = Counter(d["verdicts"][model]["native_class"] for d, _ in points)
            tally[model] = {k: classes[k] for k in ("agreement", "false_tle", "false_accept", "mixed_native")}
            tally[model]["mixed_majority_disagreements"] = sum(
                d["verdicts"][model]["native_class"] == "mixed_native"
                and d["verdicts"][model]["tle"] != (2*d["native_tle_reps"] > n) for d, n in points)
    return total


def analyze(directory):
    # The primary verifier checks raw wait4 observations, selection and packet identity,
    # live snapshots, native references, registration hashes and guest CPU placement.
    primary = verify.verify(directory)
    cases = {}
    for name, trial in primary["cases"].items():
        directory_packet = directory / "cases" / name / "deadline-packet"
        samples = []
        reused = None
        if "packet_median_ns" in trial:
            packet = verify.load(directory_packet / "packet.json")
            samples = [verify.load(directory_packet / f"policy-{i}.json")["native"]["cpu_ns"] for i in range(3)]
            reused = packet["reused_original"]
        cases[name] = score_case(trial, samples, reused)
    rows = list(cases.values())
    groups = dict(all=rows, current=[c for c in rows if c["source"] == "current"],
                  transferred=[c for c in rows if c["source"] == "transferred"],
                  prospective=[c for c in rows if c["cohort"] == "prospective"])
    return dict(schema=1, kind="exploratory_model_implied_verdicts", summary_sha256=primary["summary_sha256"],
                groups={name: summarize(batch) for name, batch in groups.items()}, cases=cases)


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("directory", type=Path)
    cli.add_argument("--output", type=Path)
    args = cli.parse_args()
    result = analyze(args.directory)
    if args.output:
        args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result["groups"], indent=2))
