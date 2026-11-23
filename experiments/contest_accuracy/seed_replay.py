"""Exploratory offline replay of one paid native calibration per contest ELF."""
import argparse
from collections import Counter
import json
from pathlib import Path
from statistics import median
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from deadline_accuracy.verify import verify
from guarded_native import anchor
from shape_calibration.verify import score_prediction


def project(estimate, samples, deadlines):
    score = score_prediction(estimate, samples, [d["limit_ms"] for d in deadlines])
    for point, registered in zip(score["limits"], deadlines, strict=True):
        point.update(fixed=registered["fixed"], adaptive=bool(registered["adaptive_percent"]))
        point["majority_match"] = bool(point["model_tle_reps"]) == (2 * point["native_tle_reps"] > len(samples))
    return score


def tally(points):
    classes = Counter(p["classification"] for p in points)
    return dict(limits=len(points), majority_matches=sum(p["majority_match"] for p in points),
                **{k: classes[k] for k in ("agreement", "false_tle", "false_accept", "mixed_native")})


def analyze(directory):
    verified = verify(directory, "exact-one", contest=True)
    plan = json.loads((HERE / "plan.json").read_bytes())
    seeds, clocks, cases = {}, {}, {}
    for row in plan["cases"]:
        if row["split"] == "calibration":
            name = row["case_id"]
            policy = directory / "cases" / name / "policy.json"
            clock = anchor.clock_from_raw(policy.read_bytes(), row["binary_sha256"])
            clocks[row["program"]] = (name, clock)
            seeds[name] = dict(policy_sha256=clock.policy_sha256, native_cpu_ns=clock.native_ns,
                               profile_cpu_ns=verified["seeds"][name]["profile_cpu_ns"])

    refusals = Counter()
    comparisons = 0
    for name, trial in verified["cases"].items():
        seed, clock = clocks[trial["program"]]
        folder = directory / "cases" / name
        current = anchor.measured(folder / "full", clock.elf_sha256)
        prediction = clock.predict_ns(current)
        selection = json.loads((folder / "baseline-selection.json").read_bytes())
        reasons = Counter()
        for comparison in selection["comparisons"]:
            for check in ("shape", "temporal", "footprint"):
                for reason in comparison[check]["reasons"]:
                    reasons[f"{check}/{reason}"] += 1
        comparisons += len(selection["comparisons"])
        refusals.update(reasons)
        actual_deadlines = [dict(limit_ms=p["limit_ms"], baseline_tle=p["baseline"]["tle"], exact_tle=p["exact"]["tle"])
                            for p in trial["deadlines"]]
        observed = dict(profile_cpu_ns=trial["profile_cpu_ns"], baseline_paid_cpu_ns=trial["baseline_paid_cpu_ns"],
                        extra_exact_cpu_ns=sum(trial["extra_ns"]), baseline_prediction_ns=trial["baseline_ns"],
                        exact_prediction_ns=trial["packet_exact_ns"], baseline_comparisons=len(selection["comparisons"]),
                        refusal_reasons=reasons, deadlines=actual_deadlines)
        cases[name] = dict(seed=seed, native_reference_ns=trial["native_ns"], observed=observed,
                           seed_projection=project(prediction, trial["native_ns"], trial["deadlines"]))

    scores = [c["seed_projection"] for c in cases.values()]
    errors = [abs(s["signed_error_ppm"]) for s in scores]
    points = [p for score in scores for p in score["limits"]]
    summary = dict(cases=len(scores), within_20pct=sum(s["within_20pct"] for s in scores),
                   median_absolute_error_ppm=median(errors), max_absolute_error_ppm=max(errors),
                   all=tally(points), fixed=tally([p for p in points if p["fixed"]]),
                   adaptive=tally([p for p in points if p["adaptive"]]),
                   overlap=tally([p for p in points if p["fixed"] and p["adaptive"]]))
    return dict(schema=1, kind="exploratory_model_implied_seed_clock", summary_sha256=verified["summary_sha256"],
                plan_sha256=verified["plan_sha256"], seeds=seeds, projection_totals=summary,
                observed_baseline_comparisons=comparisons, observed_refusal_reasons=refusals, cases=cases)


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("directory", type=Path)
    cli.add_argument("--output", type=Path)
    args = cli.parse_args()
    result = analyze(args.directory)
    if args.output:
        args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result["projection_totals"], indent=2))
