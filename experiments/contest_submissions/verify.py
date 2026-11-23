"""Replay the registered run order, diagnostic profiles and sealed submission predictions."""
import argparse
from fractions import Fraction
import json
from pathlib import Path
import re
from statistics import median
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from calibrated_limits.verify import METER, load, positive, verify_placement
from contest_submissions import bench, score
from contest_submissions.bounds import bounds
from cpp20_locality.export import parse_trace
from cpp20_locality.verify import release_reader, require, sha, verify_counters
from first_run_sampler.profile import read_json
from guarded_native import anchor


def profile(directory, row, reader):
    pid = row["launcher_pid"]
    record = directory / f"process-{pid}.jsonl"
    require(set(directory.iterdir()) == {record}, "unexpected process record set")
    raw = record.read_bytes()
    require(sha(raw) == row["record_sha256"], "profile bytes differ from journal")
    parsed = reader(directory, pid)
    verify_counters(raw, parsed, None, "diag", pid, 0, reader)
    return record, bench.counters(parsed)


def reference_only(measured):
    """Retrospective forecast assigning the reference solution's median time to the submission."""
    cases, errors = {}, {}
    for name, row in measured.items():
        estimate = median(row["reference_samples_ns"])
        native = row["native_samples_ns"]
        target = median(native)
        errors[name] = Fraction(estimate - target, target)
        cases[name] = dict(prediction_ns=estimate, signed_error_ppm=round(score.MS * errors[name]),
                           fixed_deadlines=score.deadlines(estimate, native, row["fixed_limits_ms"]))

    def summary(names):
        absolute = [abs(errors[name]) for name in names]
        points = [p for name in names for p in cases[name]["fixed_deadlines"]]
        unanimous = [p for p in points if not p["mixed"]]
        return dict(cases=len(names), median_absolute_error_ppm=round(score.MS * median(absolute)),
                    maximum_absolute_error_ppm=round(score.MS * max(absolute)), within_20pct=sum(5*e <= 1 for e in absolute),
                    fixed_limits=len(points), mixed=len(points) - len(unanimous),
                    majority_correct=sum(p["majority_correct"] for p in points),
                    unanimous_correct=sum(p["majority_correct"] for p in unanimous),
                    false_tle=sum(p["model_tle"] and not p["majority_correct"] for p in unanimous),
                    false_accept=sum(not p["model_tle"] and not p["majority_correct"] for p in unanimous))

    programs = sorted({name.split("/")[0] for name in cases})
    return dict(all=summary(cases), programs={p: summary([n for n in cases if n.startswith(p + "/")]) for p in programs},
                cases=cases)


def verify(directory):
    root = Path(directory)
    plan_raw = (HERE / "plan.json").read_bytes()
    plan = read_json(plan_raw)
    run_raw = (root / "run.json").read_bytes()
    run = read_json(run_raw)
    plan_sha = sha(plan_raw)
    require(run["schema"] == plan["schema"] == 1 and plan["authority"] == "diagnostic_only"
            and run["plan_sha256"] == plan_sha, "unknown registration")
    place = run["placement"]
    require(type(place["cpu"]) is int and place["cpu"] == plan["cpu"] == 1 and place["machine"] == "x86_64"
            and run["cgroup_allowed_cpus"] == "1"
            and isinstance(place["kernel"], str) and place["kernel"]
            and re.fullmatch("[0-9a-f]{64}", place["boot_sha256"])
            and type(run["cpu_steal_ticks"]) is int and run["cpu_steal_ticks"] >= 0,
            "invalid guest placement")
    cases = {c["case_id"]: c for c in plan["cases"]}
    require(cases and len(cases) == len(plan["cases"]), "duplicate or empty case registration")
    measured = {name: dict(reference_elf_sha256=c["reference_elf_sha256"],
                           submission_elf_sha256=c["submission_elf_sha256"], fixed_limits_ms=c["fixed_limits_ms"],
                           reference_samples_ns=[], native_samples_ns=[]) for name, c in cases.items()}
    reader = release_reader(load(anchor.PROTOCOL)["tools_sha256"]["production_reader.py"])
    rows = run["rows"]
    pids, records = {}, set()
    cutoff = None
    for sequence, (row, slot) in enumerate(zip(rows, bench.schedule(plan), strict=True)):
        require(type(row["sequence"]) is int and row["sequence"] == sequence
                and all(type(row[k]) is type(v) and row[k] == v for k, v in slot.items()),
                f"wrong execution schedule at row {sequence}")
        name, role = slot["case_id"], slot["role"]
        case, data = cases[name], measured[name]
        executable = case["program"] if role == "reference" else case["submission"]
        require(row["program"] == executable and row["elf_sha256"] == case[role + "_elf_sha256"]
                and row["stdin_sha256"] == case["stdin_sha256"] and row["stdout_sha256"] == case["stdout_sha256"],
                f"wrong executable, input or oracle at row {sequence}")
        pid = row["launcher_pid"]
        require(positive(pid) and pid not in pids and type(row["exit_code"]) is int and row["exit_code"] == 0
                and all(type(row[k]) is int and row[k] >= 0 for k in ("user_us", "sys_us", "cpu_us", "wall_us"))
                and row["user_us"] + row["sys_us"] == row["cpu_us"] > 0 and row["wall_us"] > 0,
                f"invalid PID, exit or wait4 observation at row {sequence}")
        is_target = slot["kind"] == "native" and role == "submission"
        if cutoff is None and is_target:
            cutoff = sequence
        require(cutoff is None or is_target, "training evidence follows a native submission label")
        if slot["kind"] == "profile":
            pids[pid] = METER
            key = role + "_counter"
            require(key not in data, f"duplicate {role} profile for {name}")
            record, data[key] = profile(root / "runs" / f"{sequence:03d}" / "records", row, reader)
            records.add(record)
        else:
            require("record_sha256" not in row, "unexpected native profile digest")
            pids[pid] = (executable,)
            field = "native_samples_ns" if is_target else "reference_samples_ns"
            data[field].append(row["cpu_us"] * 1000)
    require(cutoff is not None, "missing native submission labels")
    require({f for f in (root / "runs").rglob("*") if f.is_file()} == records, "unexpected archived run receipt")
    verify_placement(root, pids)
    events, _ = parse_trace((root / "exec-trace").read_text())
    commands = {command for group in pids.values() for command in group}
    traced = {pid for pid, trace in events.items() if any(e["executable"] in commands for e in trace)}
    require(traced == set(pids), "unregistered workload in exec trace")

    prediction_raw = (root / "predictions.json").read_bytes()
    sealed = read_json(prediction_raw)
    require(run["predictions_sha256"] == sha(prediction_raw) and sealed["schema"] == 1
            and sealed["boot_sha256"] == place["boot_sha256"] and sealed["plan_sha256"] == plan_sha
            and sealed["prior_rows_sha256"] == bench.rows_digest(rows[:cutoff])
            and sealed["predictions"].keys() == cases.keys(), "prediction seal differs from pre-label journal")
    for name, data in measured.items():
        data["predictions"] = sealed["predictions"][name]
    report = score.evaluate(measured)
    report["reference_only"] = reference_only(measured)
    # The submission labels enter this exploratory bound only after the frozen forecasts are checked.
    report["feature_bounds"] = {name: bounds(data["reference_counter"], data["submission_counter"],
                                             data["reference_samples_ns"], data["native_samples_ns"])
                                for name, data in measured.items()}
    report.update(plan_sha256=plan_sha, run_sha256=sha(run_raw), predictions_sha256=sha(prediction_raw),
                  placement=place, cpu_steal_ticks=run["cpu_steal_ticks"], verified_pids=len(pids),
                  profile_runs=len(records), reference_native_runs=cutoff - len(records),
                  submission_native_runs=len(rows) - cutoff)
    return report


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("directory", type=Path)
    cli.add_argument("--output", type=Path)
    args = cli.parse_args()
    report = verify(args.directory)
    if args.output:
        args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report["all"], indent=2))
