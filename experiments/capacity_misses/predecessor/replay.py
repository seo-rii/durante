"""Verify the original guest evidence and compare the pre-registered clock predictions."""
import argparse
from datetime import datetime
import importlib.util
import json
from pathlib import Path
from statistics import median
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import prepare

spec = importlib.util.spec_from_file_location("milan_receipts", prepare.MILAN / "replay.py")
milan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(milan)
FREEZE = "76c69f61afd1ee2167c137fb6b5cb76743aa7707"


def read(path):
    return json.loads(path.read_bytes())


def registered():
    plan = read(HERE / "frozen.json")
    protocol = read(HERE / "protocol.json")
    reg = read(HERE / "registration.json")
    files = {item["path"]: item["sha256"] for item in reg["files"]}
    require = milan.require
    sha = milan.sha
    require(len(files) == len(reg["files"]) and sha((HERE / "frozen.json").read_bytes()) == files["plan.json"]
            and plan["protocol"] == protocol, "plan differs from the registered guest")
    require(sha((HERE / "solve.cpp").read_bytes()) == plan["solver_sha256"]
            and sha((HERE / "prepare.py").read_bytes()) == plan["preparer_sha256"], "different solver or generator")
    parent = prepare.payload.parent_files()
    for name, digest in parent.items():
        if name != "plan.json" and not name.startswith(("inputs/", "bin/")):
            require(files[name] == digest, f"different registered tool: {name}")
    source = prepare.MILAN.parent.parents[1]
    for name, path in {"guest.py": prepare.MILAN / "guest.py", "guest.sh": prepare.MILAN / "guest.sh",
                       "collect.py": source / "collect.py", "experiments/capacity_misses/collect.py": prepare.MILAN.parent / "collect.py",
                       "experiments/capacity_misses/repeatability.py": prepare.MILAN.parent / "repeatability.py",
                       "tests/contest/problems.py": source / "tests/contest/problems.py"}.items():
        require(sha(path.read_bytes()) == files[name], "different receipt reader: " + name)
    prior = read(prepare.MILAN / "frozen.json")["weights"]["dijkstra"]
    expected_weights = {model: prior[model] for model in protocol["models"]}
    require(set(plan["cases"]) == {n for names in protocol["groups"].values() for n in names}, "case coverage")
    for pattern, names in protocol["groups"].items():
        prices, budgets = prepare.workload(protocol, pattern)
        data = prepare.encode(prices, budgets)
        answer = prepare.oracle(prices, budgets)
        require(plan["weights"][pattern] == expected_weights, "weights changed")
        for solver, name in zip(protocol["solvers"], names, strict=True):
            case = plan["cases"][name]
            require(case["stdin"] == "inputs/" + pattern and case["stdin_sha256"] == files[case["stdin"]] == sha(data)
                    and case["expected_sha256"] == sha(answer) and case["elf_sha256"] == files["bin/" + name]
                    == files["bin/" + protocol["inputs"][0] + "-" + solver], "wrong ELF, input or oracle")
    return plan, {**reg, "payload": files}


def timing_ratios(cases, plan):
    timing = {}
    for pattern, (reference, *variants) in plan["protocol"]["groups"].items():
        anchor = cases[reference]
        for name in variants:
            case = cases[name]
            native = median(case["native"]) / median(anchor["native"])
            observed = [min(case["native"]) / max(anchor["native"]), max(case["native"]) / min(anchor["native"])]
            models = {}
            for model, weights in plan["weights"][pattern].items():
                def score(observation):
                    return median(milan.predict(weights, milan.features(p)) for p in observation["probe"])
                ratio = score(case) / score(anchor)
                models[model] = dict(ratio=round(ratio, 6), relative_error_percent=round(100 * (ratio / native - 1), 2))
            timing[name] = dict(native_median_ratio=round(native, 6),
                                native_range=[round(v, 6) for v in observed], models=models)
    return timing


def replay():
    plan, reg = registered()
    cloud = read(HERE / "evidence/cloud.json")
    samples, result = {}, {}
    for field in ("boot_sha256", "instance_id", "boot_disk_id"):
        milan.require(cloud["primary"][field] != cloud["repeat"][field], "shared guest: " + field)
    for phase in ("primary", "repeat"):
        facts = cloud[phase]
        milan.require(facts["registration_commit"] == FREEZE and
                      datetime.fromisoformat(facts["started_at"]) < datetime.fromisoformat(facts["platform"]["created_at"])
                      < datetime.fromisoformat(facts["cleanup"]["verified_at"]), "cloud lifecycle order")
        cases, steal = milan.read_boot(HERE / "evidence" / (phase + ".tar.gz"), phase, plan, reg, facts)
        decisions, outcomes = milan.classify(cases, plan)
        costs = {name: round(median(c["probe_cpu"]) / median(c["native"]), 2) for name, c in cases.items()}
        result[phase] = dict(timing=timing_ratios(cases, plan), decisions=decisions, outcomes=outcomes,
                             full_probe_over_native=costs, steal_ticks=steal)
        samples[phase] = cases
    pooled = {name: {field: samples["primary"][name][field] + samples["repeat"][name][field]
                     for field in ("native", "probe")} for name in plan["cases"]}
    decisions, outcomes = milan.classify(pooled, plan)
    result["pooled"] = dict(decisions=decisions, outcomes=outcomes)
    # A distinct, retrospective comparison at the reference's own runtime.
    # Keep it out of the registered 1.25x..3x totals above.
    parity = {**plan, "protocol": {**plan["protocol"], "factors": [1]}}
    equal_decisions, equal_outcomes = milan.classify(pooled, parity)
    result["exploratory_reference_deadline"] = dict(decisions=equal_decisions, outcomes=equal_outcomes)
    result["identical_four_counter_runs"] = {name: milan.probe_repeatable(case["probe"]) for name, case in pooled.items()}
    return result


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--verify", type=Path)
    args = cli.parse_args()
    result = replay()
    if args.verify:
        milan.require(result == read(args.verify), "published comparison differs")
        print("Verified both boots, all 108 executions and all clock predictions")
    else:
        print(json.dumps(result, indent=2))
