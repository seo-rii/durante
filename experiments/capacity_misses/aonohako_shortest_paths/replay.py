"""Replay anchored research-clock decisions against the original shortest-paths deadline."""
import argparse
from collections import Counter
from datetime import datetime
from fractions import Fraction
import gzip
import importlib.util
import json
from pathlib import Path
from statistics import median

import prepare

HERE, MILAN, sha = prepare.HERE, prepare.MILAN, prepare.sha
spec = importlib.util.spec_from_file_location("milan_counter_evidence", MILAN / "replay.py")
milan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(milan)
require = milan.require
PHASES = ("primary", "repeat")
REGISTERED = "ac69db192fec95a4cbad88184db0c657f4c0bfec"
REG_SHA = "c129606b16bfc46e393ad3bc5f9f296485918d0ba939a6219bd862bcc951a706"


def read(path):
    return json.loads(path.read_bytes())


def cheap_nanoseconds(counter, clock):
    numerator = (counter["instructions"] * clock["instruction_ns_numerator"] +
                 (counter["movs_bytes"] + counter["stos_bytes"]) * clock["rep_byte_ns_numerator"])
    return -(-numerator // clock["ns_denominator"])


def interval(values):
    return min(values), max(values)


def anchored_range(submission, reference, reference_cpu):
    require(min(submission) > 0 and min(reference) > 0 and min(reference_cpu) > 0, "nonpositive anchor or ML4 score")
    return (min(submission) * min(reference_cpu) / max(reference),
            max(submission) * max(reference_cpu) / min(reference))


def verdict(bounds, limit):
    lo, hi = bounds
    if hi <= limit:
        return "accept"
    if lo > limit:
        return "timeout"
    return "indeterminate"


def compare(native, estimate):
    if "indeterminate" in (native, estimate):
        return "indeterminate"
    if native == estimate:
        return "agree"
    return "false_accept" if estimate == "accept" else "false_timeout"


def classify(samples, plan):
    protocol = plan["protocol"]
    anchor = protocol["reference"]
    limit = 1000 * protocol["deadline_us"]
    weights = {k: Fraction(str(v)) for k, v in plan["ml4"].items()}
    scores = {n: [milan.predict(weights, milan.features(p)) for p in c["probe"]] for n, c in samples.items()}
    reference_cpu = [1000 * v for v in samples[anchor]["native"]]
    cheap_scores = {n: [Fraction(cheap_nanoseconds(p, plan["original_clock"])) for p in c["probe"]]
                    for n, c in samples.items()}
    results = {}
    totals = {model: Counter() for model in ("cheap", "ML4", "anchored_cheap_secondary")}
    for name, case in samples.items():
        native = interval([1000 * v for v in case["native"]])
        cheap = tuple(int(v) for v in interval(cheap_scores[name]))
        normalized = anchored_range(cheap_scores[name], cheap_scores[anchor], reference_cpu)
        model = anchored_range(scores[name], scores[anchor], reference_cpu)
        labels = {k: verdict(bounds, limit) for k, bounds in (("native", native), ("cheap", cheap), ("ML4", model),
                                                               ("anchored_cheap_secondary", normalized))}
        for key in totals:
            if name != anchor:
                totals[key][compare(labels["native"], labels[key])] += 1
        results[name] = dict(native_ns=native, cheap_ns=cheap, ml4_ns=[round(float(v), 3) for v in model],
                             anchored_cheap_secondary_ns=[round(float(v), 3) for v in normalized],
                             verdicts=labels, native_samples=len(case["native"]), probe_samples=len(case["probe"]),
                             counters_identical=milan.probe_repeatable(case["probe"]),
                             probe_native_cpu_ratio=round(median(case["probe_cpu"]) / median(case["native"]), 3))
    return dict(cases=results, excluding_anchor=totals)


def validate_registration(plan, reg):
    require(sha((HERE / "registration.json").read_bytes()) == REG_SHA
            and sha((HERE / "frozen.json").read_bytes()) == reg["payload"]["plan.json"]
            and plan["protocol"] == read(HERE / "protocol.json"), "changed preregistration")
    previous = prepare.parent_files()
    require(plan["ml4"] == read(MILAN / "frozen.json")["weights"]["dijkstra"]["ML4"]
            and plan["aonohako_revision"] == prepare.AONO_COMMIT and plan["corrected_probe_sha256"] == previous["probe.so"],
            "different corrected meter, deployed corpus or frozen weights")
    for name, expected in previous.items():
        if name != "plan.json" and not name.startswith(("inputs/", "bin/")):
            require(reg["payload"][name] == expected, "different collector " + name)
    require(list(plan["cases"]) == plan["protocol"]["programs"] and reg["expected_rows"] == {".":63}
            and reg["payload"]["inputs/graph"] == sha(plan["input"].encode()), "altered schedule or stdin")
    for name, case in plan["cases"].items():
        require(case["elf_sha256"] == reg["payload"]["bin/" + name] and case["stdin_sha256"] == reg["payload"]["inputs/graph"]
                and case["expected_sha256"] == sha(plan["expected_stdout"].encode()), "changed ELF or oracle")



def verify_live(aonohako, plan):
    """Rebuild the historical counts from the published v13 public HTTP trial rows."""
    root = aonohako / "docs/durante/gce-shortest-paths-boundary"
    registration = json.loads(prepare.checked(root / "registration.json", prepare.PANEL))
    prepare.checked(root / "results.json", prepare.LIVE)
    for phase, cases in plan["existing_live"].items():
        data = gzip.decompress((root / "evidence/v13" / phase / "trials.jsonl.gz").read_bytes())
        rows = [json.loads(line) for line in data.splitlines()]
        for name, expected in cases.items():
            cell = [r for r in rows if r["case_id"] == name and r["boundary"] == "contest"]
            require(len(cell) == 5 and all(r["time_ms"] == 65 and r["binary_sha256"] == plan["cases"][name]["elf_sha256"]
                    and r["stdin_sha256"] == plan["cases"][name]["stdin_sha256"]
                    and r["request_sha256"] == registration["requests"]["v13"][name+"/contest/"+r["kind"]]
                    for r in cell), "different historical submission or limits")
            native = Counter(r["receipt"]["status"] for r in cell if r["kind"] == "native")
            clock = [r["receipt"] for r in cell if r["kind"] == "shared"]
            causes = Counter(r["work_measurement_execution"]["termination_source"] for r in clock)
            require(expected == dict(native=native, clock=Counter(r["status"] for r in clock), clock_termination=causes),
                    "historical live receipts differ")


def replay():
    reg, plan = read(HERE / "registration.json"), read(HERE / "frozen.json")
    validate_registration(plan, reg)
    cloud = read(HERE / "evidence/cloud.json")
    previous = read(MILAN / "evidence/cloud.json")
    for ident in ("boot_sha256", "instance_id", "boot_disk_id"):
        current = {c[ident] for c in cloud.values()}
        require(len(current) == 2 and current.isdisjoint(c[ident] for c in previous.values()), "reused guest " + ident)
    panels, result = {}, {}
    for phase in PHASES:
        c = cloud[phase]
        require(c["registration_commit"] == REGISTERED and datetime.fromisoformat(c["started_at"])
                < datetime.fromisoformat(c["platform"]["created_at"]) < datetime.fromisoformat(c["cleanup"]["verified_at"]),
                "invalid guest chronology")
        panel, steal = milan.read_boot(HERE / "evidence" / (phase + ".tar.gz"), phase, plan, reg, c)
        panels[phase] = panel
        result[phase] = dict(**classify(panel, plan), steal_ticks=steal)
    pooled = {n: {field: panels["primary"][n][field] + panels["repeat"][n][field]
                  for field in ("native", "probe", "probe_cpu")} for n in plan["cases"]}
    result["pooled"] = classify(pooled, plan)
    result["prior_live_v13"] = plan["existing_live"]
    return result


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--verify", type=Path)
    cli.add_argument("--aonohako", type=Path, help="also check the original live HTTP rows against the registered digest index")
    args = cli.parse_args()
    if args.aonohako:
        verify_live(args.aonohako, read(HERE / "frozen.json"))
    report = json.dumps(replay(), indent=2, sort_keys=True)
    if args.verify:
        require(json.loads(report) == read(args.verify), "published result differs")
        print("Verified both GCE boots, raw process counters and 65 ms comparisons")
    else:
        print(report)
