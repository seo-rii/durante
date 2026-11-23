"""Replay the interleaved calibration, paired work-clock enforcements and independent CPU references."""
import argparse
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from calibrated_limits.verify import METER, load, positive, verify_placement
from cpp20_locality.export import parse_trace
from cpp20_locality.verify import release_reader, require, sha
from deadline_accuracy.registration import INTERLEAVED_STUDIES, registration_for
from deadline_accuracy.verify import aggregate, annotate, baseline, exact, live, read_packet, thresholds
from guarded_native import anchor
from guarded_native.score import MS
from shape_calibration import temporal
from shape_calibration.verify import native_reference


def check_packets(root, cases, bindings, trials, kernel, boot, register):
    directory = root / "batch"
    ids = [case["case_id"] for case in cases]
    ordered = sorted(ids, key=lambda name: sha(name.encode()))
    rounds = [ordered[r % len(ids):] + ordered[:r % len(ids)] for r in range(3)]
    placement = dict(cpu=1, machine="x86_64", kernel=kernel, boot_sha256=boot)
    expected_files = {directory / "manifest.json"}
    entries, payments, selected = [], {}, {}
    for case in cases:
        name = case["case_id"]
        obs, source = bindings[name]
        folder = directory / name
        # Each packet is stored in input order. Defer PID registration to physical round order.
        physical = []
        def record(pid, commands):
            physical.append((pid, commands))
        chosen, extra = read_packet(folder, source, obs, case, kernel, boot, record, "warm")
        require(len(physical) == len(extra) == 3, "three current-input payments required")
        payments[name], selected[name] = physical, chosen
        trials[name].update(extra_ns=extra, packet_warm_ns=chosen[2]["native_ns"])
        packet = folder / "packet.json"
        entries.append(dict(id=name, elf_sha256=obs["elf_sha256"], stdin_sha256=obs["stdin_sha256"],
                            current_result_sha256=obs["result_sha256"],
                            source_selection_sha256=sha(source[0].read_bytes()), source_policy_sha256=source[2]["hash"],
                            packet_sha256=sha(packet.read_bytes()), selection_sha256=sha(chosen[0].read_bytes())))
        # read_packet has validated the exact member set inside this input's directory.
        expected_files.update(folder.iterdir())
    require({p for p in directory.rglob("*") if p.is_file()} == expected_files, "unexpected batch file")
    exact(load(directory / "manifest.json"), dict(schema=1, authority="diagnostic_only", strategy="interleaved-warm-three",
                                                placement=placement, cases=entries, rounds=rounds),
          "batch manifest differs from the registered bindings, placement or round schedule")
    for index, names in enumerate(rounds):
        for name in names:
            register(*payments[name][index])
    return selected


def check_limits(root, seq, case, row, bindings, trial, plan, reader, register, *, check_windows):
    name = case["case_id"]
    obs, source, packet = bindings
    events = iter(row["limits"])
    registered = thresholds(trial["baseline_ns"], plan["deadline_percent"], case["fixed_limits_ms"])
    for index, (ms, fixed, percents) in enumerate(registered):
        near = 4 * abs(trial["baseline_ns"] - ms * MS) <= ms * MS
        point = dict(limit_ms=ms, near=near, fixed=fixed, adaptive_percent=percents)
        arms = (("baseline", source), ("warm", packet))
        for arm, binding in (arms if (seq + index) % 2 == 0 else reversed(arms)):
            event = next(events, None)
            require(isinstance(event, dict), "missing paired live enforcement")
            exact([event[k] for k in ("index", "limit_ms", "method", "near")], [index, ms, arm, near],
                  "different deadline, arm order or near-boundary flag")
            if check_windows and arm == "warm":
                lo, hi = min(trial["extra_ns"]), max(trial["extra_ns"])
                window = dict(min_ns=lo, max_ns=hi, split=lo <= ms * MS < hi)
                exact(event.get("calibration_window"), window, "different three-sample calibration window")
                point["calibration_window"] = window
            else:
                require("calibration_window" not in event, "unexpected calibration window")
            dest = root / "cases" / name / "limits" / arm / f"{index:02d}"
            point[arm] = live(root, dest, event, binding, obs, case, plan, reader, register)
        trial["deadlines"].append(point)
    require(next(events, None) is None, "extra paired live enforcement")


def verify(directory):
    root = Path(directory)
    summary = load(root / "summary.json")
    # Select only a checked-in registration. The envelope below binds its rules and every input.
    matches = [study for study in INTERLEAVED_STUDIES
               if sha((HERE.parent / study / "plan.json").read_bytes()) == summary["plan_sha256"]]
    require(len(matches) == 1, "unknown interleaved plan digest")
    raw_plan, raw_rules, plan = registration_for("interleaved-three", study=matches[0])
    rules = json.loads(raw_rules)
    require(summary["plan_sha256"] == sha(raw_plan) and summary["rules_sha256"] == sha(raw_rules)
            and summary["probe_sha256"] == temporal.PROBE_SHA == rules["probe_sha256"] == plan["probe_sha256"]
            and summary["schema"] == rules["schema"] == plan["schema"] == 1
            and summary["affinity"] == [1] and summary["cgroup_cpus"] == "1"
            and isinstance(summary["kernel"], str) and summary["kernel"]
            and type(summary["steal_delta"]) is int and summary["steal_delta"] >= 0, "different registration or guest")
    kernel, cases = summary["kernel"], plan["cases"]
    reader = release_reader(load(anchor.PROTOCOL)["tools_sha256"]["production_reader.py"])
    pids = {}
    def register(pid, commands):
        require(positive(pid) and pid not in pids, "duplicate physical workload PID")
        pids[pid] = commands

    trials, bindings, boot_ids = {}, {}, set()
    for seq, (case, row) in enumerate(zip(cases, summary["rows"], strict=True)):
        name = case["case_id"]
        exact([row["sequence"], row["case_id"]], [seq, name], "case order mismatch")
        require(name not in trials and case["split"] == "holdout", "duplicate or ineligible case")
        inp = case["stdin"].encode()
        require((root / "inputs" / sha(inp)).read_bytes() == inp, "different registered input")
        obs, source, trial = baseline(root / "cases", case, row, {}, [], {}, kernel, register, boot_ids)
        bindings[name] = obs, source
        trials[name] = dict(**trial, cohort=case["cohort"], program=case["program"])

    boot, = boot_ids
    selected = check_packets(root, cases, bindings, trials, kernel, boot, register)
    for seq, (case, row) in enumerate(zip(cases, summary["rows"], strict=True)):
        name = case["case_id"]
        check_limits(root, seq, case, row, (*bindings[name], selected[name]), trials[name], plan, reader, register,
                     check_windows="boundary_diagnostic" in rules)

    ordered = sorted(cases, key=lambda case: sha(case["case_id"].encode()))
    samples = native_reference(root, summary, plan, ordered, register)
    verify_placement(root, pids)
    trace, _ = parse_trace((root / "exec-trace").read_text())
    commands = set(METER) | {case["program"] for case in cases}
    actual = {pid for pid, events in trace.items() if any(e["executable"] in commands for e in events)}
    require(actual == set(pids), "unaccounted measured process in the trace")

    for name, trial in trials.items():
        annotate(trial, samples[name], "warm")
    return dict(schema=1, plan_sha256=summary["plan_sha256"], rules_sha256=summary["rules_sha256"],
                summary_sha256=sha((root / "summary.json").read_bytes()), verified_pids=len(pids),
                native_runs=len(summary["native"]), steal_ticks=summary["steal_delta"], seeds={},
                totals={cohort: aggregate([t for t in trials.values() if cohort == "all" or t["cohort"] == cohort], "warm")
                        for cohort in ("all", "prospective", "previous")},
                cases={case["case_id"]: trials[case["case_id"]] for case in ordered})


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("directory", type=Path)
    cli.add_argument("--output", type=Path)
    args = cli.parse_args()
    result = verify(args.directory)
    if args.output:
        args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result["totals"], indent=2))
