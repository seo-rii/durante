"""Replay Scrivener input scans, counter readings, predictions and deadline errors."""
import argparse
from collections import defaultdict
from fractions import Fraction
from functools import cache
import json
from pathlib import Path
import re
from statistics import median
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from calibrated_limits.verify import METER, positive, verify_placement
from cpp20_locality.export import parse_trace
from cpp20_locality.verify import release_reader, require, sha
from first_run_sampler.profile import read_json
from scrivener_pages import bench


@cache
def registered_cases(plan_raw, study=bench.STUDY):
    return {case["name"]: case for case, _ in bench.check_inputs(read_json(plan_raw), study)}


def qualification(cases, limits, baseline="branch", candidate="branch_pages"):
    """Compare exact errors and changes at individual unanimous deadline decisions."""
    changes = {key: [] for key in ("false_pass_fixed", "false_pass_added", "false_timeout_fixed", "false_timeout_added")}
    worst = {baseline: Fraction(0), candidate: Fraction(0)}
    for name, programs in cases.items():
        for program, case in programs.items():
            center = case["median_native_ns"]
            estimates = case["predictions_ns"]
            for model in worst:
                worst[model] = max(worst[model], abs(Fraction(estimates[model] - center, center)))
            for ms in limits:
                votes = case["native_tle_votes"][str(ms)]
                if 0 < votes < len(case["native_ns"]):
                    continue
                threshold = ms * 1_000_000
                before = estimates[baseline] > threshold
                after = estimates[candidate] > threshold
                if before != after:
                    error = "false_pass" if votes else "false_timeout"
                    change = "fixed" if after == bool(votes) else "added"
                    changes[f"{error}_{change}"].append([name, program, ms])
    return dict(accuracy_gate=worst[candidate] < worst[baseline],
                deadline_gate=len(changes["false_pass_fixed"]) > len(changes["false_pass_added"])
                and not changes["false_timeout_added"], unanimous_error_changes=changes)


def verify(root, study=bench.STUDY, qualify=qualification):
    plan_raw = (study.directory / "plan.json").read_bytes()
    plan = read_json(plan_raw)
    run_raw = (root / "run.json").read_bytes()
    run = read_json(run_raw)
    plan_sha = sha(plan_raw)
    require(type(run["schema"]) is int and run["schema"] == plan["schema"] == 1
            and run["authority"] == plan["authority"] == "diagnostic_only" and run["plan_sha256"] == plan_sha
            and run["tools"] == plan["tools"] and set(run["tools"]) == set(bench.TOOL_PATHS), "wrong registration or tools")
    require((plan["scan_reps"], plan["profile_reps"], plan["native_reps"]) == (3, 2, 9)
            and set(plan["binaries"]) == {"scrivener", "scrivener_lifting"}, "unexpected repetitions or solvers")
    host = run["placement"]
    require(type(host["cpu"]) is int and host["cpu"] == plan["cpu"] == 1 and run["cgroup_allowed_cpus"] == "1"
            and host["machine"] == "x86_64" and re.fullmatch(r"[0-9a-f]{64}", host["boot_sha256"])
            and isinstance(host["kernel"], str) and host["kernel"] and type(run["cpu_steal_ticks"]) is int
            and run["cpu_steal_ticks"] >= 0, "invalid guest placement")
    cases = registered_cases(plan_raw, study)
    reader = release_reader(plan["tools"]["reader"])
    rows = run["rows"]
    pids = {}
    expected_files = {root / filename for filename in ("exec-trace", "trace-stats", "run.json", "predictions.json")}
    samples = defaultdict(list)
    cutoff = None
    for seq, (row, slot) in enumerate(zip(rows, bench.schedule(plan), strict=True)):
        require(type(row["sequence"]) is int and row["sequence"] == seq
                and all(type(row[k]) is type(v) and row[k] == v for k, v in slot.items()), "wrong scheduled row")
        name, program, arm = (slot[k] for k in ("name", "program", "arm"))
        case = cases[name]
        scan = arm == "scan"
        require(row["stdin_sha256"] == case["stdin_sha256"]
                and row["stdout_sha256"] == case["scan_sha256" if scan else "stdout_sha256"]
                and row["elf_sha256"] == (plan["tools"]["scan"] if scan else plan["binaries"][program]),
                "changed input, ELF or stdout")
        pid = row["launcher_pid"]
        require(positive(pid) and pid not in pids and type(row["exit_code"]) is int and row["exit_code"] == 0
                and all(type(row[k]) is int and row[k] >= 0 for k in ("cpu_us", "user_us", "sys_us", "wall_us"))
                and row["cpu_us"] == row["user_us"] + row["sys_us"] > 0 and row["wall_us"] > 0, "invalid wait4 row")
        directory = root / "runs" / f"{seq:03d}"
        if arm == "ordinary":
            chain, files = METER, (f"records/process-{pid}.jsonl",)
        elif arm == "branch":
            chain, files = ("valgrind.bin", "cachegrind-amd64-linux"), ("cachegrind.out", "cachegrind.log")
        elif scan:
            chain, files = ("scan",), ("scan.out",)
        else:
            require(arm == "native" and "features" not in row, "unexpected native features or arm")
            chain, files = (program,), ()
            if case["group"] != "anchor" and cutoff is None:
                cutoff = seq
        pids[pid] = chain
        digests = {file: sha((directory / file).read_bytes()) for file in files}
        require(digests == row["receipts"], "mismatched raw receipt inventory or digest")
        expected_files.update(directory / file for file in files)
        if scan:
            require(digests["scan.out"] == case["scan_sha256"], "unexpected raw scanner output")
        if arm != "native":
            values = bench.measured_features(arm, directory, pid, program, reader, study)
            require(row["features"] == values and all(type(v) is int for v in row["features"].values()),
                    "count differs from raw receipt")
        samples[name, program, arm].append(row)

    paths = list(root.rglob("*"))
    require(not any(p.is_symlink() for p in paths) and {p for p in paths if p.is_file()} == expected_files,
            "unexpected file in guest archive")
    verify_placement(root, pids)
    trace = (root / "exec-trace").read_text()
    events, timeline = parse_trace(trace)
    exe_names = {exe for chain in pids.values() for exe in chain}
    require(len(timeline) == sum("sched_process_exec:" in line for line in trace.splitlines()) and
            {pid for pid, chain in events.items() if any(e["executable"] in exe_names for e in chain)} == set(pids),
            "unregistered measured process or malformed exec event")
    seal_raw = (root / "predictions.json").read_bytes()
    seal = read_json(seal_raw)
    require(cutoff is not None and run["predictions_sha256"] == sha(seal_raw) and seal["plan_sha256"] == plan_sha
            and seal["boot_sha256"] == host["boot_sha256"] and seal["prefix_sha256"] == bench.rows_digest(rows[:cutoff]),
            "pre-label seal does not match the calibration journal")
    require(seal["cases"] == bench.predictions(plan, rows[:cutoff], study), "sealed forecasts differ from replay")
    report = study.scoring.summarize(plan, rows, seal["cases"])

    for name in cases:
        scans = [r["features"] for r in samples[name, "scan", "scan"]]
        require(len(scans) == plan["scan_reps"] and all(v == scans[0] for v in scans), "input scan changed across repeats")
        for program in plan["binaries"]:
            native = [r["cpu_us"] * 1000 for r in samples[name, program, "native"]]
            require(len(native) == plan["native_reps"], "missing native run")
            center = median(native)
            result = report["cases"].setdefault(name, {}).setdefault(program, {})
            result.update(native_ns=native, median_native_ns=center, native_min_ns=min(native), native_max_ns=max(native),
                          native_spread_ppm=round(1_000_000 * Fraction(max(native) - min(native), center)),
                          native_tle_votes={str(ms): sum(ns > ms * 1_000_000 for ns in native) for ms in plan["fixed_limits_ms"]},
                          features=dict(scans[0]), feature_spread={})
            for arm, field in (("ordinary", "work"), ("branch", "bcm")):
                values = [r["features"][field] for r in samples[name, program, arm]]
                require(len(values) == plan["profile_reps"], "missing profile")
                result["features"][field] = values[0]
                result["feature_spread"][field] = max(values) - min(values)
    holdouts = {name: report["cases"][name] for name in cases if cases[name]["group"] == "holdout"}
    if study.controls:
        report["controls"] = {name: report["cases"].pop(name) for name in study.controls}
    report["qualification"] = qualify(holdouts, plan["fixed_limits_ms"])
    return dict(schema=1, authority="diagnostic_only", plan_sha256=plan_sha, run_sha256=sha(run_raw),
                predictions_sha256=sha(seal_raw), boot_sha256=host["boot_sha256"],
                cpu_steal_ticks=run["cpu_steal_ticks"], verified_pids=len(pids), sealed_prefix_rows=cutoff, **report)


def main(replay=verify):
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("directory", type=Path)
    cli.add_argument("--output", type=Path)
    args = cli.parse_args()
    metrics = replay(args.directory)
    if args.output:
        args.output.write_text(json.dumps(metrics, indent=2) + "\n")
    print(json.dumps(metrics["summary"], indent=2))


if __name__ == "__main__":
    main()
