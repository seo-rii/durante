"""Replay the held-out maximum-subarray panel with three frozen clock estimates."""
import argparse
from fractions import Fraction
import json
from pathlib import Path
import runpy
from statistics import median

HERE = Path(__file__).resolve().parent
lcs = runpy.run_path(str(HERE.parent / "lcs_layout/replay.py"))
inputs = runpy.run_path(str(HERE / "inputs.py"))
read, require, sha = lcs["read"], lcs["require"], lcs["sha"]
Interval, Observation = lcs["Interval"], lcs["intervals"]["Observation"]
OLDER = HERE.parent / "range_distinct"
FREEZE = "a3605470692248ef20751ddf593cc268f1084a0f"
REG = "c5878b2bf35e103339e34004a863e802af718441039b1b4ef86d99a7edc40e94"


def registered():
    reg = read(HERE / "registration.json")
    plan = read(HERE / "frozen.json")
    proto = plan["protocol"]
    manifest = reg["payload"]
    require(sha((HERE / "registration.json").read_bytes()) == REG and
            sha((HERE / "frozen.json").read_bytes()) == manifest["plan.json"] and
            read(HERE / "protocol.json") == proto and reg["expected_rows"] == {".": 78}, "changed registration")
    old_reg = read(OLDER / "registration.json")["payload"]
    def shared(files):
        return {n: h for n, h in files.items() if n != "plan.json" and not n.startswith(("bin/", "inputs/"))}
    original = read(OLDER / "frozen.json")
    require(shared(manifest) == shared(old_reg) and
            all(plan[k] == original[k] for k in ("ml4", "gshare_miss_ns", "clients", "flags")) and
            all(proto[k] == original["protocol"][k] for k in ("rounds", "clients", "modes", "factors")),
            "different tools, coefficients, limits or schedule")
    require(proto["intercept_ns"] == 7_000_000, "different score-floor value")
    for name, h in plan["source_sha256"].items():
        require(sha((HERE / name).read_bytes()) == h, "different source: " + name)
    for family, names in proto["groups"].items():
        pattern, text = inputs["workload"](family)
        stdin, answer = inputs["encode"](pattern, text), inputs["answer"](pattern, text)
        for name in names:
            case = plan["cases"][name]
            first = "short-" + name.split("-")[1]
            require(sha(stdin) == manifest[case["stdin"]] == case["stdin_sha256"] and
                    sha(answer) == case["expected_sha256"] and
                    manifest["bin/" + name] == manifest["bin/" + first] == case["elf_sha256"],
                    "changed compiler output, input or independent answer")
    return reg, plan


def score_ratio(submission, reference, method, fixed):
    if method == "baseline":
        offset = 0
    elif method == "global":
        offset = fixed
    elif method == "floor":
        offset = max(0, fixed - reference)
    else:
        raise ValueError("unknown score correction")
    return (submission + offset) / (reference + offset)


def evaluate(phase, panel, proto, method):
    rows, ratios = [], {}
    for anchor, *alternatives in proto["groups"].values():
        ref_cpu, ref_scores = panel[anchor]
        for name in alternatives:
            cpu, scores = panel[name]
            value = [score_ratio(s, r, method, proto["intercept_ns"]) for s in scores for r in ref_scores]
            rows.append(Observation("maxsub_floor", phase, name, Interval.from_runs(cpu, ref_cpu),
                                    Interval(min(value), max(value))))
            native = median(cpu) / median(ref_cpu)
            estimate = score_ratio(median(scores), median(ref_scores), method, proto["intercept_ns"])
            ratios[name] = (native, estimate, estimate / native - 1)
    result = lcs["intervals"]["evaluate"](rows, Interval(Fraction(1), Fraction(1)),
                                          [Fraction(str(v)) for v in proto["factors"]])
    for name, (native, estimate, error) in ratios.items():
        result["cases"][name].update(median_native=str(native), median_prediction=str(estimate),
                                     signed_relative_error=str(error))
    errors = [abs(v[2]) for v in ratios.values()]
    result["median_absolute_relative_error"] = str(median(errors))
    result["within_20pct"] = sum(e <= Fraction(1, 5) for e in errors)
    return result


def replay():
    reg, plan = registered()
    proto = plan["protocol"]
    panels, steal, telemetry = lcs["measured_panels"](HERE, plan, reg, FREEZE)
    result = {}
    for phase, panel in panels.items():
        scores = {model: evaluate(phase, panel, proto, model) for model in ("baseline", "global", "floor")}
        refs = {group: panel[names[0]][1] for group, names in proto["groups"].items()}
        scores["reference_ns_range"] = {g: [str(min(v)), str(max(v))] for g, v in refs.items()}
        scores["reference_scales_straddle_floor"] = max(refs["short"]) < proto["intercept_ns"] < min(refs["long"])
        scores["meets_criterion"] = lcs["criterion"](scores["baseline"], scores["floor"])
        result[phase] = scores
    return dict(panels=result, telemetry=telemetry, steal_ticks=steal,
                passes_on_each_boot=all(result[p]["meets_criterion"] for p in ("primary", "repeat")))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
    result = replay()
    if args.verify:
        require(result == read(args.verify), "different published maximum-subarray results")
        print("Both 78-run guests, maximum-subarray answers and all three registered clocks reproduced.")
    else:
        print(json.dumps(result, indent=2))
