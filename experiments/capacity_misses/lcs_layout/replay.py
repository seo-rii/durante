"""Replay the frozen absolute-score intercept on the LCS layout panel."""
import argparse
from fractions import Fraction
import json
from pathlib import Path
import runpy
from statistics import median

HERE = Path(__file__).resolve().parent
OLDER = HERE.parent / "range_distinct"
INTERVALS = HERE.parent / "relative_intervals"
inputs = runpy.run_path(str(HERE / "inputs.py"))
clock = runpy.run_path(str(OLDER / "replay.py"))
intervals = runpy.run_path(str(INTERVALS / "replay.py"))
read, require, sha = clock["read"], clock["require"], clock["sha"]
Interval = intervals["Interval"]
FREEZE = "f11d7d8000ba81f10dad2283a25d432cd3ca516a"
REG = "6f0e7ca99e6856a8195d3005f6b11ce5bab55a2274a61269a367635d2cc4a24e"


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
    require(proto["intercept_ns"] == 7_000_000, "different candidate intercept")
    for name, h in plan["source_sha256"].items():
        require(sha((HERE / name).read_bytes()) == h, "different source: " + name)
    for family, names in proto["groups"].items():
        pattern, text = inputs["workload"](family)
        stdin, answer = inputs["encode"](pattern, text), inputs["answer"](pattern, text)
        for name in names:
            case = plan["cases"][name]
            first = "wide-" + name.split("-")[1]
            require(sha(stdin) == manifest[case["stdin"]] == case["stdin_sha256"] and
                    sha(answer) == case["expected_sha256"] and
                    manifest["bin/" + name] == manifest["bin/" + first] == case["elf_sha256"],
                    "changed compiler output, input or independent answer")
    return reg, plan


def evaluate(phase, observations, proto, offset):
    limits = [Fraction(str(x)) for x in proto["factors"]]
    estimates = {name: (native, [s + offset for s in scores]) for name, (native, scores) in observations.items()}
    rows = [row for group in proto["groups"].values()
            for row in intervals["record"]("lcs_layout", phase, group, estimates)]
    quality = intervals["evaluate"](rows, Interval(Fraction(1), Fraction(1)), limits)
    errors = []
    for anchor, *alternatives in proto["groups"].values():
        base_native, base_scores = estimates[anchor]
        for name in alternatives:
            native, scores = estimates[name]
            observed = median(native) / median(base_native)
            prediction = median(scores) / median(base_scores)
            error = prediction / observed - 1
            quality["cases"][name].update(median_native=str(observed), median_prediction=str(prediction),
                                           signed_relative_error=str(error))
            errors.append(abs(error))
    quality["median_absolute_relative_error"] = str(median(errors))
    quality["within_20pct"] = sum(e <= Fraction(1, 5) for e in errors)
    return quality


def criterion(baseline, candidate):
    return (Fraction(candidate["median_absolute_relative_error"]) < Fraction(baseline["median_absolute_relative_error"])
            and all(candidate["counts"][k] <= baseline["counts"][k] for k in ("false_accept", "false_timeout")))


def replay():
    reg, plan = registered()
    proto, prior = plan["protocol"], clock["previous"]
    weights = {k: Fraction(str(v)) * 10**9 for k, v in plan["ml4"].items()}
    penalty = Fraction(str(plan["gshare_miss_ns"]))
    evidence = HERE / "evidence"
    cloud = read(evidence / "cloud.json")
    for key in ("instance_id", "boot_disk_id", "boot_sha256"):
        require(len({v[key] for v in cloud.values()}) == 2, "reused guest identity")
    clients = {a: plan["clients"][n] for a, n in proto["clients"].items()}
    panels, steal, work = {}, {}, {}
    for phase in ("primary", "repeat"):
        arms, steal[phase] = prior["read_boot"](phase, cloud[phase], reg, plan, evidence=evidence, freeze=FREEZE,
            client_by_arm=clients, diagnostic_by_arm={"outer": "outer", "flat": "outer", "branch": "latency"})
        prior["read_branch"](evidence / (phase + ".tar.gz"), arms)
        panel, work[phase] = {}, {}
        for name, measurements in arms.items():
            scores, telemetry = clock["measured"](measurements, weights, penalty)
            panel[name] = ([Fraction(r["cpu_us"]) for r in measurements["native"]], scores["ML4_plus_misses"])
            work[phase][name] = dict(cpu_us=telemetry["cpu_us_median"], input_misses=telemetry["misses"],
                                      features=telemetry["features"], score_ns=str(median(scores["ML4_plus_misses"])))
        panels[phase] = panel
    panels["pooled"] = {name: (n + panels["repeat"][name][0], s + panels["repeat"][name][1])
                        for name, (n, s) in panels["primary"].items()}
    out = {}
    for phase, panel in panels.items():
        baseline = evaluate(phase, panel, proto, 0)
        candidate = evaluate(phase, panel, proto, proto["intercept_ns"])
        out[phase] = dict(baseline=baseline, candidate=candidate, meets_criterion=criterion(baseline, candidate))
    return dict(panels=out, passes_on_each_boot=all(out[p]["meets_criterion"] for p in ("primary", "repeat")),
                telemetry=work, steal_ticks=steal)


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--verify", type=Path)
    args = cli.parse_args()
    result = replay()
    if args.verify:
        require(result == read(args.verify), "published LCS scores differ")
        print("Verified 156 executions, both inputs and both registered timing estimates.")
    else:
        print(json.dumps(result, indent=2))
