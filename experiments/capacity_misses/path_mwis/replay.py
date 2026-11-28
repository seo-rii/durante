"""Score both registered weighted-path guests and compare their deadline decisions."""
import argparse
from fractions import Fraction
import json
from pathlib import Path
import runpy
from statistics import median

HERE = Path(__file__).resolve().parent
OLDER = HERE.parent / "range_distinct"
clock = runpy.run_path(str(OLDER / "replay.py"))
intervals = runpy.run_path(str(HERE.parent / "relative_intervals/replay.py"))
inputs = runpy.run_path(str(HERE / "inputs.py"))
read, sha, require = clock["read"], clock["sha"], clock["require"]
FREEZE = "c2dabd9e9560e9f5e77279c6322933a2d38e7a9e"
REGISTRATION = "d424afb28d287c4382025b69aff09074e350e66ec28ffcf597bdd620cf47db13"


def registered():
    plan, reg = read(HERE / "frozen.json"), read(HERE / "registration.json")
    proto, manifest = plan["protocol"], reg["payload"]
    require(sha((HERE / "registration.json").read_bytes()) == REGISTRATION and
            sha((HERE / "frozen.json").read_bytes()) == manifest["plan.json"] and
            proto == read(HERE / "protocol.json") and reg["expected_rows"] == {".": 78},
            "changed path study registration")
    old = read(OLDER / "frozen.json")
    old_tools = read(OLDER / "registration.json")["payload"]
    for name, digest in old_tools.items():
        if name != "plan.json" and not name.startswith(("bin/", "inputs/")):
            require(manifest[name] == digest, "different tool: " + name)
    require(all(old[k] == plan[k] for k in ("ml4", "gshare_miss_ns", "clients", "flags")) and
            all(old["protocol"][k] == proto[k] for k in ("rounds", "modes", "clients", "factors")),
            "different baseline or measurement settings")
    for name, digest in plan["source_sha256"].items():
        require(sha((HERE / name).read_bytes()) == digest, "different registered source: " + name)
    for group, names in proto["groups"].items():
        args = inputs["workload"](group)
        stdin, expected = sha(inputs["encode"](*args)), sha(inputs["answer"](*args))
        for name in names:
            case = plan["cases"][name]
            require(stdin == case["stdin_sha256"] == manifest[case["stdin"]] and
                    expected == case["expected_sha256"] and case["elf_sha256"] == manifest["bin/" + name],
                    "different executable, input or answer")
    return plan, reg


def recurrence_scores(cache_scores, branch_samples, miss_penalty, recurrence_weight):
    # The two predictor fields come from the same target invocation.
    return [cache + miss_penalty * row["branch"]["gshare_mispredicts"] +
            recurrence_weight * row["loop_recurrence_cycles_x16"]
            for cache in cache_scores for row in branch_samples]


def evaluate(phase, proto, samples):
    deadlines = [Fraction(str(v)) for v in proto["factors"]]
    obs = [row for names in proto["groups"].values()
           for row in intervals["record"]("path_mwis", phase, names, samples)]
    result = intervals["evaluate"](obs, intervals["Interval"](Fraction(1), Fraction(1)), deadlines)
    errors = []
    for anchor, *others in proto["groups"].values():
        ref_cpu, ref_score = samples[anchor]
        for name in others:
            cpu, score = samples[name]
            native, prediction = median(cpu) / median(ref_cpu), median(score) / median(ref_score)
            error = prediction / native - 1
            errors.append(abs(error))
            result["cases"][name].update(median_native=str(native), median_prediction=str(prediction),
                                         signed_relative_error=str(error))
    result["median_absolute_relative_error"] = str(median(errors))
    result["within_20_percent"] = sum(e <= Fraction(1, 5) for e in errors)
    return result


def new_errors(before, after):
    return [[name, index] for name, row in before["cases"].items()
            for index, (old, new) in enumerate(zip(row["outcomes"], after["cases"][name]["outcomes"], strict=True))
            if new.startswith("false_") and old != new]


def replay():
    plan, reg = registered()
    proto, tools = plan["protocol"], clock["previous"]
    weight = Fraction(proto["recurrence_ns_per_x16"])
    penalty = Fraction(str(plan["gshare_miss_ns"]))
    weights = {k: Fraction(str(v)) * 10**9 for k, v in plan["ml4"].items()}
    evidence = HERE / "evidence"
    cloud = read(evidence / "cloud.json")
    require(set(cloud) == {"primary", "repeat"} and
            all(cloud["primary"][k] != cloud["repeat"][k] for k in ("instance_id", "boot_disk_id", "boot_sha256")),
            "expected two independent guests")
    clients = {arm: plan["clients"][name] for arm, name in proto["clients"].items()}
    results = {}
    for phase, host in cloud.items():
        arms, steal = tools["read_boot"](phase, host, reg, plan, evidence=evidence, freeze=FREEZE,
            client_by_arm=clients, diagnostic_by_arm={"flat":"outer", "outer":"outer", "branch":"latency"})
        tools["read_branch"](evidence / (phase + ".tar.gz"), arms, include_recurrence=True)
        samples = {"baseline": {}, "recurrence": {}}
        telemetry = {}
        for name, runs in arms.items():
            scores, _ = clock["measured"](runs, weights, penalty)
            cpu = [Fraction(r["cpu_us"]) for r in runs["native"]]
            samples["baseline"][name] = cpu, scores["ML4_plus_misses"]
            enhanced = recurrence_scores(scores["ML4"], runs["branch"], penalty, weight)
            samples["recurrence"][name] = cpu, enhanced
            telemetry[name] = dict(native_us=[int(t) for t in cpu],
                                   recurrence_x16=[r["loop_recurrence_cycles_x16"] for r in runs["branch"]],
                                   baseline_ns=[str(s) for s in scores["ML4_plus_misses"]],
                                   recurrence_ns=[str(s) for s in enhanced])
        baseline, candidate = [evaluate(phase, proto, samples[k]) for k in ("baseline", "recurrence")]
        regressions = new_errors(baseline, candidate)
        improved = Fraction(candidate["median_absolute_relative_error"]) < Fraction(baseline["median_absolute_relative_error"])
        results[phase] = dict(baseline=baseline, recurrence=candidate, new_errors=regressions,
                              meets_criterion=improved and not regressions, steal_ticks=steal, telemetry=telemetry)
    return dict(boots=results, passes_both=all(r["meets_criterion"] for r in results.values()))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
    result = replay()
    if args.verify:
        require(result == read(args.verify), "different published weighted-path results")
        print("Verified both pinned guests, recurrence measurements, ratios and deadline decisions.")
    else:
        print(json.dumps(result, indent=2))
