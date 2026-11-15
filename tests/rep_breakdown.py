#!/usr/bin/env python3
"""Qualify exact mixed MOVS/STOS demand across native lifecycle transitions."""
import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess

from concurrent_spawn import bounded_number, cleanup_orphans, own_subreaper
from jit_lifecycle import diagnostic_classification as jit_diagnostic_classification
from runtime_compat import InputProvenance, bounded_json, collect_records, run_process

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = "rep-breakdown-ok\n"
KINDS = ("movs_bytes", "stos_bytes", "movs_entries", "stos_entries")
UNIT = (65, 66, 3, 3)
# (compiler MODE, process count, per-process image work units). A fork/vfork
# child's counters start at zero; an exec replaces the image but keeps its PID.
CASES = {
    "ordinary": (0, 1, [[1]]),
    "fork": (1, 2, [[4], [2]]),
    "exec": (2, 1, [[1, 2]]),
    "failed-exec": (3, 1, [[3]]),
    "thread": (4, 1, [[6]]),
    "vfork": (5, 2, [[4], [2]]),
}
MODES = [("native", None, False), ("supervisor", "release", True)] + [
    (label, core, rep) for core in ("release", "debug")
    for label, rep in (("engine", False), ("counter-off", False), ("counter-on", True))]


def diagnostic_classification(row):
    # This fixture has exactly one successful exec: the debug core initializes
    # its CPU description in both images. CI retained the same known cache-size
    # notice once per image, with correct output and exact REP partitions.
    # Fork/vfork and failed exec do not introduce another engine startup here.
    if (row.get("fixture") == "exec" and row.get("core") == "debug"
            and row.get("execution_mode") in ("engine", "counter-off", "counter-on")
            and row.get("stderr") == "<Unknown processor cache size>\n" * 2):
        return "cpu_cache_size_unknown_after_exec"
    # Keep the existing single-image policy, including rejection of any other
    # diagnostic or repetition. Never strip or rewrite the retained stderr.
    return jit_diagnostic_classification(row)


def expected_breakdown(units, enabled):
    return {"version": 1, **{key: count * units if enabled else 0 for key, count in zip(KINDS, UNIT)}}


def accounting_errors(measurement, fixture, enabled):
    _, process_count, work = CASES[fixture]
    errors = []
    if measurement.get("valid") is not True or measurement.get("complete") is not True:
        return ["measurement unavailable"]
    processes, segments = measurement.get("processes", []), measurement.get("segments", [])
    if measurement.get("process_count") != process_count or len(processes) != process_count or len(segments) != sum(map(len, work)):
        return ["wrong process/image coverage"]
    root = measurement.get("root_pid")
    ordered = sorted(processes, key=lambda row: row["pid"] != root)
    expected_images = []
    for process, units in zip(ordered, work):
        rows = [row for row in segments if row["pid"] == process["pid"]]
        expected_images += [(row, count) for row, count in zip(rows, units)]
        if len(rows) != len(units) or process.get("images") != len(units):
            errors.append("wrong per-process image coverage")
        expected_images.append((process, sum(units)))
    expected_images.append((measurement, sum(sum(units) for units in work)))
    for row, units in expected_images:
        expected = expected_breakdown(units, enabled)
        actual = row.get("rep_breakdown")
        if (not isinstance(actual, dict) or actual.keys() != expected.keys()
                or any(type(actual[key]) is not int or actual[key] != value for key, value in expected.items())):
            errors.append("incorrect MOVS/STOS breakdown")
        if row.get("rep_bytes") != expected["movs_bytes"] + expected["stos_bytes"] or row.get("rep_entries") != expected["movs_entries"] + expected["stos_entries"]:
            errors.append("incorrect combined REP demand")
        if row.get("unsupported_rep_entries") != 0 or (fixture not in ("fork", "vfork") and row.get("signals_observed") != 0):
            errors.append("unexpected unsupported work or signal")
    if fixture == "thread" and segments[0].get("threads") != 2:
        errors.append("thread work did not reach terminal aggregation")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("sdk", "client", "runner-bin", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--timeout-ms", type=bounded_number(1000, 30000), default=5000)
    args = parser.parse_args()
    for name in ("sdk", "client", "runner_bin", "output_dir"):
        setattr(args, name, getattr(args, name).resolve())
    compiler, limiter = shutil.which("gcc"), shutil.which("prlimit")
    if not compiler or not limiter:
        parser.error("gcc and prlimit required")
    args.prlimit = str(Path(limiter).absolute())
    args.output_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    inputs = {name: ROOT / name for name in ("tests/rep_breakdown.S", "tests/rep_breakdown.py",
              "tests/runtime_compat.py", "tests/concurrent_spawn.py", "tests/jit_lifecycle.py", "collect.py")}
    inputs.update(client=args.client, runner=args.runner_bin, compiler=Path(compiler),
                  drrun=args.sdk / "bin64/drrun", release_core=args.sdk / "lib64/release/libdynamorio.so",
                  debug_core=args.sdk / "lib64/debug/libdynamorio.so")
    provenance = InputProvenance(inputs)
    spec = importlib.util.spec_from_file_location("rep_reference_collector", ROOT / "collect.py")
    reference = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reference)
    rows, failures = [], []
    libc = previous = None
    cleaned = False
    try:
        provenance.capture("before")
        if provenance.errors:
            raise ValueError("qualification input identity unavailable")
        libc, previous = own_subreaper()
        for fixture, (mode, _, _) in CASES.items():
            executable = args.output_dir / fixture
            setup = args.output_dir / ("setup-" + fixture)
            setup.mkdir(mode=0o700)
            compiled = run_process([compiler, "-static", "-nostdlib", "-no-pie", f"-DMODE={mode}",
                                    str(ROOT / "tests/rep_breakdown.S"), "-o", str(executable)], setup, 20000, args.prlimit)
            if not compiled["infrastructure_ok"] or compiled["exit_code"] != 0 or compiled.get("stderr"):
                raise ValueError("fixture compilation failed: " + fixture)
            for label, core, rep in MODES:
                case = f"{fixture}-{label}-{core or 'none'}"
                directory = args.output_dir / case
                directory.mkdir(mode=0o700)
                records = directory / "records"
                command = [str(executable)]
                instrumented = label in ("counter-off", "counter-on", "supervisor")
                if label == "supervisor":
                    command = [str(args.runner_bin), "run", "--drrun", str(inputs["drrun"]),
                               "--client", str(args.client), "--report", str(directory / "report.json"),
                               "--records-dir", str(records), "--timeout-ms", str(args.timeout_ms), "--", *command]
                elif core:
                    prefix = [str(inputs["drrun"]), *(["-debug"] if core == "debug" else []),
                              "-quiet", "-follow_children", "-vm_size", "32M", "-vmheap_size", "32M", "-stderr_mask", "12"]
                    if instrumented:
                        records.mkdir(mode=0o700)
                        prefix += ["-c", str(args.client), "-outdir", str(records)]
                        if rep:
                            prefix += ["-rep_bytes"]
                    command = [*prefix, "--", *command]
                row = run_process(command, directory, args.timeout_ms + 5000, args.prlimit)
                row.update(case=case, fixture=fixture, execution_mode=label, core=core, rep_enabled=rep)
                errors = row["errors"]
                row["functional_ok"] = row["exit_code"] == 0 and row.get("stdout") == OUTPUT
                if not row["functional_ok"]:
                    errors.append("native memory/output oracle failed")
                row["diagnostic_classification"] = diagnostic_classification(row)
                if row["diagnostic_classification"] == "unexpected":
                    errors.append("unexpected execution diagnostic")
                if instrumented:
                    if label == "supervisor":
                        envelope = bounded_json(directory / "report.json")
                        measurement, execution = envelope["measurement"], envelope["execution"]
                        row["execution"] = execution
                        if execution.get("state") != "complete" or execution.get("cleanup_complete") is not True or execution.get("records_cleanup_complete") is not True:
                            errors.append("supervisor cleanup incomplete")
                        root_pid = execution["root_pid"]
                    else:
                        root_pid = row["root_pid"]
                        measurement = collect_records(args, directory, root_pid)
                    row["measurement"] = measurement
                    accounting = accounting_errors(measurement, fixture, rep)
                    if reference.collect(records, root_pid) != measurement:
                        accounting.append("Rust/Python collector mismatch")
                    row["accounting_ok"] = not accounting
                    errors.extend(accounting)
                row["passed"] = row["infrastructure_ok"] and not errors
                rows.append(row)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as error:
        failures.append(f"harness failure: {type(error).__name__}: {error}")
    finally:
        if libc is not None:
            try:
                cleaned = cleanup_orphans()
            except OSError:
                failures.append("cannot inspect harness cleanup")
            if libc.prctl(36, previous, 0, 0, 0):
                cleaned = False
        provenance.capture("after")
    if not cleaned or not provenance.unchanged():
        failures.append("incomplete cleanup or changed inputs")
    if len(rows) != len(CASES) * len(MODES) or any(not row["passed"] for row in rows):
        failures.append("incomplete or failed qualification matrix")
    summary = {"schema": 1, "passed": not failures, "cases": len(rows), "failures": failures,
               "cleanup_complete": cleaned, "inputs_unchanged": provenance.unchanged(), "rows": rows}
    (args.output_dir / "results.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({key: value for key, value in summary.items() if key != "rows"}))
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
