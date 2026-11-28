"""Independent inversion validation of the two-counter score frozen in Durante #125."""
import argparse
from fractions import Fraction
import json
from pathlib import Path
from statistics import median
import sys

HERE = Path(__file__).resolve().parent
STUDY = HERE.parent
MODELS = ("ML4", "two_counter")
OUTCOMES = ("correct", "false_accept", "false_timeout", "indeterminate")


def decision(values, reference, factor):
    if max(values) <= factor * min(reference):
        return "accept"
    if min(values) > factor * max(reference):
        return "timeout"
    return "indeterminate"


def outcome(actual, model):
    if "indeterminate" in (actual, model):
        return "indeterminate"
    if actual == model:
        return "correct"
    return "false_accept" if model == "accept" else "false_timeout"


def round6(num):
    return round(float(num), 6)


def endpoints(values, reference):
    return [round6(Fraction(min(values))/max(reference)), round6(Fraction(max(values))/min(reference))]


def compare(variants, groups, factors, observations):
    summary = {m:dict.fromkeys(OUTCOMES, 0) for m in MODELS}
    cases, errors = {}, {m:[] for m in MODELS}
    for group in variants:
        anchor, *evaluated = groups[group]
        ref = observations[anchor]
        for name in evaluated:
            sample = observations[name]
            native = Fraction(median(sample["native"])) / Fraction(median(ref["native"]))
            row = cases[name] = dict(reference=anchor, native_ratio=round6(native),
                                     native_bounds=endpoints(sample["native"], ref["native"]), models={}, deadlines={})
            for model in MODELS:
                predicted = Fraction(median(sample[model])) / Fraction(median(ref[model]))
                error = predicted/native - 1
                errors[model].append(abs(error))
                row["models"][model] = dict(score_ratio=round6(predicted), score_bounds=endpoints(sample[model], ref[model]),
                                            signed_error_percent=round6(100*error),
                                            within_20_percent=abs(error) <= Fraction(1, 5))
            for factor in factors:
                f = Fraction(str(factor))
                actual = decision(sample["native"], ref["native"], f)
                d = row["deadlines"][str(factor)] = {"native": actual}
                for model in MODELS:
                    predicted = decision(sample[model], ref[model], f)
                    d[model] = predicted
                    summary[model][outcome(actual, predicted)] += 1
    for model, e in errors.items():
        summary[model].update(within_20_percent=sum(v <= Fraction(1,5) for v in e), timing_cases=len(e),
                              median_absolute_percent_error=round6(100*median(e)))
    return dict(summary=summary, cases=cases)


def replay():
    # Run the parent study's pinned registration and original record parser. Keep
    # the prospective witness separate from the models in its original protocol.
    sys.path.insert(0, str(STUDY))
    import replay as collection
    reg, plan, _ = collection.registered()
    protocol = plan["protocol"]
    exact_weights = json.loads((STUDY / "frozen.json").read_bytes(), parse_float=Fraction)["ml4"]
    hosts = collection.read(STUDY / "evidence/cloud.json")
    client = {arm:plan["clients"][n] for arm,n in protocol["clients"].items()}
    panels = {}
    for phase in collection.PHASES:
        raw, _ = collection.read_boot(phase, hosts[phase], reg, plan, evidence=STUDY/"evidence",
                                      freeze=collection.FREEZE, client_by_arm=client,
                                      diagnostic_by_arm={"outer":"outer","flat":"outer","branch":"latency"})
        panel = {}
        for case, arms in raw.items():
            counts = [collection.ml4_features(r["counters"]) for a in ("outer", "flat") for r in arms[a]]
            collection.require(counts and all(c == counts[0] for c in counts), "different full-probe readings")
            panel[case] = dict(native=[r["cpu_us"] for r in arms["native"]],
                               ML4=[sum(c[k]*w for k,w in exact_weights.items()) for c in counts],
                               two_counter=[c["latency_x16"] + 700*c["l1d_dependent"] for c in counts])
        panels[phase] = panel
    panels["pooled"] = {n:{arm:panels["primary"][n][arm]+panels["repeat"][n][arm] for arm in data}
                        for n,data in panels["primary"].items()}
    result = {}
    for phase, panel in panels.items():
        result[phase] = {label:compare(groups, protocol["groups"], protocol["factors"], panel)
                         for label,groups in (("predecessor_development", protocol["training_examples"]),
                                              ("inversion_holdout", protocol["holdout"]))}
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
    data = replay()
    if args.verify:
        if data != json.loads(args.verify.read_bytes()):
            raise SystemExit("published comparison differs")
        print("Verified every development and holdout case, verdict and timing error on both boots.")
    else:
        print(json.dumps(data, indent=2))
