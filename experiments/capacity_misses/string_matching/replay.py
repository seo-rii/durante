"""Replay the registered string-matching matrix with the exact prior residual interval."""
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
FREEZE = "a53ace8e962011b692a5f86beca4c30711f9d961"
REG = "51045979b49abb8f87096dcf0d6e139cb0b9ba8b8806678e99b0709dfc43a8ed"


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
    origin = proto["interval_source"]
    published = read(INTERVALS / "results.json")[origin["key"]]
    require(origin["key"] == "layered_with_dijkstra" and origin["training"] == published["training_families"] and
            origin["multipliers"] == published["multiplier"], "different historical interval")
    for name, h in plan["source_sha256"].items():
        require(sha((HERE / name).read_bytes()) == h, "different source: " + name)
    for family, names in proto["groups"].items():
        pattern, text = inputs["workload"](family)
        stdin, answer = inputs["encode"](pattern, text), inputs["answer"](pattern, text)
        for name in names:
            case = plan["cases"][name]
            first = "random-" + name.split("-")[1]
            require(sha(stdin) == manifest[case["stdin"]] == case["stdin_sha256"] and
                    sha(answer) == case["expected_sha256"] and
                    manifest["bin/" + name] == manifest["bin/" + first] == case["elf_sha256"],
                    "changed compiler output, input or independent answer")
    return reg, plan


def evaluate(rows, correction, limits):
    point = intervals["evaluate"](rows, Interval(Fraction(1), Fraction(1)), limits)
    adjusted = intervals["evaluate"](rows, correction, limits)
    decisions = {}
    for row in rows:
        scaled = row.model.times(correction)
        decisions[row.case] = {
            str(limit): dict(native=row.native.verdict(limit), point=row.model.verdict(limit),
                             interval=scaled.verdict(limit) if scaled.verdict(limit) != "mixed" else "abstain")
            for limit in limits}
    calls = [cell for by_limit in decisions.values() for cell in by_limit.values()]
    correct_accept = sum(c["native"] == c["interval"] == "accept" for c in calls)
    correct_timeout = sum(c["native"] == c["interval"] == "timeout" for c in calls)
    counts = adjusted["counts"]
    passed = all((correct_accept, correct_timeout)) and not any(counts[k] for k in
                ("false_timeout", "false_accept", "issued_native_mixed"))
    return dict(point=point, adjusted=adjusted, decisions=decisions, correct_accept=correct_accept,
                correct_timeout=correct_timeout, passes_bidirectional_criterion=passed)


def replay():
    reg, plan = registered()
    proto = plan["protocol"]
    limits = [Fraction(str(x)) for x in proto["factors"]]
    correction = Interval(*map(Fraction, proto["interval_source"]["multipliers"]))
    weights = {k: Fraction(str(v)) * 10**9 for k, v in plan["ml4"].items()}
    penalty = Fraction(str(plan["gshare_miss_ns"]))
    evidence = HERE / "evidence"
    cloud = read(evidence / "cloud.json")
    for k in ("instance_id", "boot_disk_id", "boot_sha256"):
        require(len({v[k] for v in cloud.values()}) == 2, "reused guest")
    clients = {arm: plan["clients"][filename] for arm, filename in proto["clients"].items()}
    prior = clock["previous"]
    samples, steal, diagnostics = {}, {}, {}
    for phase in ("primary", "repeat"):
        arms, steal[phase] = prior["read_boot"](phase, cloud[phase], reg, plan, evidence=evidence, freeze=FREEZE,
            client_by_arm=clients, diagnostic_by_arm={"outer": "outer", "flat": "outer", "branch": "latency"})
        prior["read_branch"](evidence / (phase + ".tar.gz"), arms)
        samples[phase] = {}
        terms = {}
        for name, measurements in arms.items():
            scores, telemetry = clock["measured"](measurements, weights, penalty)
            features = telemetry["features"]
            terms[name] = {key: weight * features[key] for key, weight in weights.items()}
            misses = [r["branch"]["gshare_mispredicts"] for r in measurements["branch"]]
            terms[name]["gshare"] = penalty * Fraction(median(misses))
            require(sum(terms[name].values()) == median(scores["ML4_plus_misses"]), "score terms disagree")
            samples[phase][name] = ([r["cpu_us"] for r in measurements["native"]], scores["ML4_plus_misses"])
        # After the prospective evaluation, decompose the two largest mechanisms
        # reported in the interpretation. Neither the scoring rule nor the
        # registered verdicts use these single-term removals.
        diagnostics[phase] = {}
        for group, alternative, omitted in (("random", "random-z", "stream_2m"), ("periodic", "periodic-naive", "gshare")):
            reference = terms[proto["groups"][group][0]]
            variant = terms[alternative]
            total, base = sum(variant.values()), sum(reference.values())
            diagnostics[phase][alternative] = dict(
                score_ratio=str(total / base),
                excess_by_term={k: str((variant[k] - v) / base) for k, v in reference.items()},
                removed_term=omitted,
                ratio_without_term=str((total - variant[omitted]) / (base - reference[omitted])))
    samples["pooled"] = {name: (n + samples["repeat"][name][0], s + samples["repeat"][name][1])
                         for name, (n, s) in samples["primary"].items()}
    out = dict(multiplier=correction.exact(), steal_ticks=steal, boots={}, exploratory_score_terms=diagnostics)
    for phase, observations in samples.items():
        rows = [r for group in proto["groups"].values()
                for r in intervals["record"]("string_matching", phase, group, observations)]
        result = evaluate(rows, correction, limits)
        if phase == "pooled":
            out["pooled"] = result
        else:
            out["boots"][phase] = result
    out["passes_on_each_boot"] = all(r["passes_bidirectional_criterion"] for r in out["boots"].values())
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
    result = replay()
    if args.verify:
        require(result == read(args.verify), "different published string-matching result")
        print("Verified both 78-run guests, 128-match oracles, exact decisions and the bidirectional criterion.")
    else:
        print(json.dumps(result, indent=2))
