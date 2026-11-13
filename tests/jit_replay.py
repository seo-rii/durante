#!/usr/bin/env python3
"""Strict fixed-oracle checks for code-cache replay and partial REP progress."""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess

from concurrent_spawn import bounded_number, cleanup_orphans, own_subreaper
from runtime_compat import (InputProvenance, bounded_bytes, bounded_json, collect_records,
                            file_identity, inspect_accounting, run_process)

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = "jit-replay-ok\n"
FIXTURES = {
    "scalar-other": {"case": 0, "generated_instructions": 4, "rep_entries": 1, "rep_bytes": 6},
    "self-backward": {"case": 1, "generated_instructions": 6, "rep_entries": 1, "rep_bytes": 13},
    "rep-forward": {"case": 2, "generated_instructions": 4, "rep_entries": 2, "rep_bytes": 16},
    "rep-backward": {"case": 3, "generated_instructions": 4, "rep_entries": 2, "rep_bytes": 16},
    "rep-stos": {"case": 4, "generated_instructions": 4, "rep_entries": 2, "rep_bytes": 16},
    "rewrite-threshold": {"case": 5, "generated_instructions": 26, "rep_entries": 13, "rep_bytes": 78},
}
EXECUTIONS = [("native", None, False), ("supervisor", "release", True)] + [
    (label, core, rep) for core in ("release", "debug")
    for label, rep in (("engine", False), ("counter-off", False), ("counter-on", True))]


def expected_instructions(disassembly, fixture):
    """Expand fixed source loops/calls from independent static disassembly."""
    expected = FIXTURES[fixture]
    active = stopped = False
    count = 0
    loop_start = loop_end = None
    for line in disassembly.splitlines():
        label = re.match(r"^[0-9a-f]+ <([^>]+)>:$", line)
        if label:
            name = label[1]
            if name == "_start":
                if active:
                    raise ValueError("duplicate fixture entry")
                active = True
            elif name == "failure":
                stopped = True
                break
            elif active and name == "rewrite_loop":
                loop_start = count
            elif active and name == "rewrite_loop_end":
                loop_end = count
        elif active and re.match(r"^\s*[0-9a-f]+:\s+\S+", line):
            count += 1
    if not active or not stopped or count < 20:
        raise ValueError("fixture success-path disassembly is incomplete")
    if fixture == "rewrite-threshold":
        if loop_start is None or loop_end is None or loop_end - loop_start != 13:
            raise ValueError("twelve-iteration rewrite loop is incomplete or changed")
        count += 11 * (loop_end - loop_start)
    elif loop_start is not None or loop_end is not None:
        raise ValueError("unexpected rewrite loop")
    return count + expected["generated_instructions"]


def accounting_errors(measurement, expected, fixture, rep):
    oracle = FIXTURES[fixture]
    fields = {"valid": True, "complete": True, "instructions": expected,
              "rep_bytes": oracle["rep_bytes"] if rep else 0,
              "rep_entries": oracle["rep_entries"] if rep else 0,
              "unsupported_rep_entries": 0, "signals_observed": 0}
    return [f"unexpected {key}: expected {value}" for key, value in fields.items()
            if type(measurement.get(key)) is not type(value) or measurement[key] != value]


def matrix_errors(rows):
    expected = {(fixture, label, core) for fixture in FIXTURES
                for label, core, _ in EXECUTIONS}
    observed = set()
    errors = []
    for row in rows:
        key = (row.get("fixture"), row.get("execution_mode"), row.get("core"))
        if key not in expected or key in observed:
            errors.append("unexpected or duplicate observation")
        observed.add(key)
        if row.get("passed") is not True:
            errors.append("failed observation: " + str(row.get("case")))
    if observed != expected:
        errors.append("incomplete observation matrix")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("sdk", "client", "runner-bin", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--timeout-ms", type=bounded_number(1000, 30000), default=5000)
    args = parser.parse_args()
    for name in ("sdk", "client", "runner_bin", "output_dir"):
        setattr(args, name, getattr(args, name).resolve())
    compiler, objdump, limiter = (shutil.which(name) for name in ("gcc", "objdump", "prlimit"))
    if not compiler or not objdump or not limiter:
        parser.error("gcc, objdump and prlimit are required")
    args.prlimit = str(Path(limiter).absolute())
    args.output_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    inputs = {name: ROOT / name for name in (
        "tests/jit_replay.S", "tests/jit_replay.py", "tests/test_jit_replay.py",
        "tests/runtime_compat.py", "tests/concurrent_spawn.py", "tests/fixtures.py", "collect.py",
        "client/durante.c", "client/durante_runtime.h", "toolkit/build_runtime.py",
        "toolkit/patches/dynamorio-vfork-lifetime.patch",
        "toolkit/patches/dynamorio-code-replay.patch")}
    inputs.update(client=args.client, runner=args.runner_bin,
                  drrun=args.sdk / "bin64/drrun", compiler=Path(compiler),
                  objdump=Path(objdump), prlimit=Path(args.prlimit),
                  runtime_manifest=args.sdk / ".durante-runtime.json",
                  release_core=args.sdk / "lib64/release/libdynamorio.so",
                  debug_core=args.sdk / "lib64/debug/libdynamorio.so")
    provenance = InputProvenance(inputs)
    rows, fixtures, failures = [], [], []
    libc = previous = None
    cleaned = False
    try:
        provenance.capture("before")
        if provenance.errors:
            raise ValueError("qualification input identity unavailable before execution")
        libc, previous = own_subreaper()
        for fixture, oracle in FIXTURES.items():
            executable = args.output_dir / fixture
            setup = args.output_dir / ("setup-" + fixture)
            setup.mkdir(mode=0o700)
            compiled = run_process([compiler, "-static", "-nostdlib", "-no-pie",
                                   f"-DCASE={oracle['case']}",
                                   str(ROOT / "tests/jit_replay.S"), "-o", str(executable)],
                                  setup, 20000, args.prlimit)
            if compiled["exit_code"] != 0 or not compiled["infrastructure_ok"] or compiled.get("stderr"):
                raise ValueError("bounded fixture compilation failed: " + fixture)
            assembly_dir = setup / "disassembly"
            assembly_dir.mkdir(mode=0o700)
            disassembly = run_process([objdump, "-d", "--no-show-raw-insn", str(executable)],
                                      assembly_dir, 5000, args.prlimit)
            if disassembly["exit_code"] != 0 or not disassembly["infrastructure_ok"] or disassembly.get("stderr"):
                raise ValueError("bounded fixture disassembly failed: " + fixture)
            expected = expected_instructions(disassembly["stdout"], fixture)
            identity = file_identity(executable)
            fixtures.append({"fixture": fixture, "expected_instructions": expected,
                             "rep_entries": oracle["rep_entries"], "rep_bytes": oracle["rep_bytes"],
                             "sha256": identity["sha256"]})
            provenance.files["fixture/" + fixture] = {
                "path": str(executable), "before": identity, "after": None, "unchanged": False}
            for label, core, rep in EXECUTIONS:
                case = f"{fixture}-{label}-{core or 'none'}"
                directory = args.output_dir / case
                directory.mkdir(mode=0o700)
                records = directory / "records"
                command = [str(executable)]
                instrumented = label.startswith("counter") or label == "supervisor"
                if label == "supervisor":
                    command = [str(args.runner_bin), "run", "--drrun", str(inputs["drrun"]),
                               "--client", str(args.client), "--report", str(directory / "report.json"),
                               "--records-dir", str(records), "--timeout-ms", str(args.timeout_ms),
                               "--", *command]
                elif core:
                    prefix = [str(inputs["drrun"]), *(["-debug"] if core == "debug" else []),
                              "-quiet", "-follow_children", "-vm_size", "32M", "-vmheap_size", "32M",
                              "-stderr_mask", "12"]
                    if instrumented:
                        records.mkdir(mode=0o700)
                        prefix += ["-c", str(args.client), "-outdir", str(records)]
                        if rep:
                            prefix += ["-rep_bytes"]
                    command = [*prefix, "--", *command]
                row = run_process(command, directory, args.timeout_ms + (5000 if label == "supervisor" else 0),
                                  args.prlimit)
                row.update(case=case, fixture=fixture, execution_mode=label, core=core,
                           expected_instructions=expected, rep_bytes_enabled=rep)
                errors = row["errors"]
                if row["exit_code"] != 0 or row.get("stdout") != OUTPUT:
                    errors.append("fixture output or exit oracle failed")
                if row.get("stderr"):
                    errors.append("unexpected runtime or application diagnostic")
                if instrumented:
                    try:
                        if label == "supervisor":
                            envelope = bounded_json(directory / "report.json")
                            row["report"] = envelope
                            execution = envelope.get("execution", {})
                            measurement = envelope.get("measurement", {})
                            root_pid = execution.get("root_pid")
                            if (execution.get("state") != "complete" or execution.get("exit_code") != 0
                                    or execution.get("cleanup_complete") is not True
                                    or execution.get("records_cleanup_complete") is not True):
                                errors.append("supervisor execution or cleanup incomplete")
                        else:
                            root_pid = row["root_pid"]
                            measurement = collect_records(args, directory, root_pid)
                            if bounded_bytes(directory / "collector-stderr.txt"):
                                errors.append("unexpected collector diagnostic")
                        row["measurement"] = measurement
                        errors.extend(inspect_accounting(measurement, records, root_pid, 1, rep)[0])
                        errors.extend(accounting_errors(measurement, expected, fixture, rep))
                    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
                        errors.append("accounting unavailable: " + str(error)[:300])
                row["passed"] = row["infrastructure_ok"] and not errors
                rows.append(row)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError,
            IndexError, subprocess.SubprocessError) as error:
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
    unchanged = provenance.unchanged()
    if not unchanged:
        failures.append("qualification input changed or unavailable")
    if not cleaned:
        failures.append("descendant cleanup incomplete")
    failures.extend(matrix_errors(rows))
    code = int(bool(failures))
    summary = {"schema": 1, "authority": "diagnostic_only", "integrity_verified": False,
               "exit_code": code, "passed": code == 0, "cases": len(rows),
               "checks_passed": sum(row["passed"] for row in rows),
               "controls_passed": sum(row["passed"] for row in rows
                                      if row["execution_mode"] in ("native", "engine")),
               "cleanup_complete": cleaned, "inputs_unchanged": unchanged,
               "input_provenance": {"files": provenance.files, "errors": provenance.errors},
               "fixtures": fixtures, "failures": failures, "rows": rows,
               "limitations": ["fixed single-threaded Linux x86-64 instruction/REP oracles",
                               "partial REP bytes mean initial requested work, not completed transfers",
                               "no accepted overcount signatures or missing-case exceptions",
                               "not concurrent JIT, runtime GC/deoptimization, or isolate qualification",
                               "diagnostic application-mutable counts, not trusted CPU-time conversion"]}
    (args.output_dir / "results.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({key: value for key, value in summary.items()
                      if key not in ("rows", "input_provenance", "fixtures")}))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
