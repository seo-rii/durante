#!/usr/bin/env python3
"""Bounded generated-code replacement checks with independent instruction oracles."""
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
MODES = ("protect", "remap", "rwx-rewrite")
OUTPUT = "jit-lifecycle-ok\n"


def expected_instructions(disassembly, extra_nops):
    """Count the assembled static success path, then expand fixed loops/calls."""
    active = False
    count = 0
    stopped = False
    for line in disassembly.splitlines():
        if re.match(r"^[0-9a-f]+ <_start>:$", line):
            active = True
        elif re.match(r"^[0-9a-f]+ <failure>:$", line):
            stopped = True
            break
        elif active and re.match(r"^\s*[0-9a-f]+:\s+\S+", line):
            count += 1
    if not active or not stopped or count < 20:
        raise ValueError("fixture success-path disassembly is incomplete")
    # Two five-instruction loops, ten executions each; two generated bodies
    # each execute MOV+RET ten times. Only code_b gains the extra one-byte NOPs.
    return count + 2 * 9 * 5 + 2 * 10 * 2 + 10 * extra_nops


def accounting_errors(measurement, expected, extra_nops, rep):
    errors = []
    for key, value in {"instructions": expected,
                       "rep_bytes": 12 + extra_nops if rep else 0,
                       "rep_entries": 2 if rep else 0,
                       "unsupported_rep_entries": 0, "signals_observed": 0}.items():
        if type(measurement.get(key)) is not type(value) or measurement[key] != value:
            errors.append(f"unexpected {key}: expected {value}")
    return errors


def accounting_status(measurement, expected, extra_nops, rep, mode):
    if measurement.get("valid") is not True or measurement.get("complete") is not True:
        return "regression"
    if not accounting_errors(measurement, expected, extra_nops, rep):
        return "qualified"
    return "regression"


def diagnostic_classification(row):
    if row.get("stderr", "") == "":
        return "none"
    # The pinned core's proc_set_cache_size reports an unenumerated CPUID cache
    # size in INTERNAL/debug builds. This does not change the fixture oracle.
    # Accept only its exact standalone line in the fixed direct debug controls;
    # other messages, repeated notices and release/native output still fail.
    if (row.get("core") == "debug"
            and row.get("execution_mode") in ("engine", "counter-off", "counter-on")
            and row.get("stderr") == "<Unknown processor cache size>\n"):
        return "cpu_cache_size_unknown"
    return "unexpected"


def execution_errors(row):
    errors = []
    if row["exit_code"] != 0 or row.get("stdout") != OUTPUT:
        errors.append("fixture did not execute both generated-code versions correctly")
    if diagnostic_classification(row) == "unexpected":
        errors.append("unexpected runtime or application diagnostic")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("sdk", "client", "runner-bin", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--timeout-ms", type=bounded_number(1000, 30000), default=5000)
    parser.add_argument("--require-qualified-mode", choices=MODES, action="append", default=[],
                        help="explicitly require this mode; every matrix row is strict by default")
    args = parser.parse_args()
    for name in ("sdk", "client", "runner_bin", "output_dir"):
        setattr(args, name, getattr(args, name).resolve())
    compiler, objdump = shutil.which("gcc"), shutil.which("objdump")
    if not compiler or not objdump or not shutil.which("prlimit"):
        parser.error("gcc, objdump and prlimit are required")
    args.prlimit = str(Path(shutil.which("prlimit")).absolute())
    args.output_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    inputs = {name: ROOT / name for name in (
        "tests/jit_lifecycle.S", "tests/jit_lifecycle.py", "tests/test_jit_lifecycle.py",
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
        for mode_id, mode in enumerate(MODES):
            for nops in (0, 32):
                executable = args.output_dir / f"{mode}-{nops}"
                setup = args.output_dir / f"setup-{mode}-{nops}"
                setup.mkdir(mode=0o700)
                compiled = run_process([compiler, "-static", "-nostdlib", "-no-pie",
                                f"-DMODE={mode_id}", f"-DEXTRA_NOPS={nops}",
                                str(ROOT / "tests/jit_lifecycle.S"), "-o", str(executable)],
                               setup, 20000, args.prlimit)
                if compiled["exit_code"] != 0 or not compiled["infrastructure_ok"] or compiled.get("stderr"):
                    raise ValueError("bounded fixture compilation failed")
                assembly_dir = setup / "disassembly"
                assembly_dir.mkdir(mode=0o700)
                disassembly = run_process([objdump, "-d", "--no-show-raw-insn", str(executable)],
                                          assembly_dir, 5000, args.prlimit)
                if disassembly["exit_code"] != 0 or not disassembly["infrastructure_ok"] or disassembly.get("stderr"):
                    raise ValueError("bounded fixture disassembly failed")
                expected = expected_instructions(disassembly["stdout"], nops)
                identity = file_identity(executable)
                fixtures.append({"mode": mode, "extra_nops": nops,
                                 "expected_instructions": expected, "sha256": identity["sha256"]})
                provenance.files[f"fixture/{mode}/{nops}"] = {
                    "path": str(executable), "before": identity, "after": None, "unchanged": False}
                modes = [("native", None, False), ("supervisor", "release", True)]
                modes += [(label, core, rep) for core in ("release", "debug")
                          for label, rep in (("engine", False), ("counter-off", False), ("counter-on", True))]
                for label, core, rep in modes:
                    case = f"{mode}-{nops}-{label}-{core or 'none'}"
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
                        # drrun launcher options must precede forwarded engine
                        # options; a late -debug is ignored by the release core.
                        prefix = [str(inputs["drrun"]), *(["-debug"] if core == "debug" else []),
                                  "-quiet", "-follow_children",
                                  "-vm_size", "32M", "-vmheap_size", "32M",
                                  "-stderr_mask", "12"]
                        if instrumented:
                            records.mkdir(mode=0o700)
                            prefix += ["-c", str(args.client), "-outdir", str(records)]
                            if rep:
                                prefix += ["-rep_bytes"]
                        command = [*prefix, "--", *command]
                    row = run_process(command, directory, args.timeout_ms + (5000 if label == "supervisor" else 0),
                                      args.prlimit)
                    row.update(case=case, mode=mode, extra_nops=nops, execution_mode=label,
                               core=core, expected_instructions=expected)
                    row["diagnostic_classification"] = diagnostic_classification(row)
                    errors = row["errors"]
                    errors.extend(execution_errors(row))
                    status = "control_passed"
                    if instrumented:
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
                        status = accounting_status(measurement, expected, nops, rep, mode)
                        if status == "regression":
                            errors.extend(accounting_errors(measurement, expected, nops, rep))
                            errors.append("instruction or REP oracle failed")
                    row["policy_passed"] = row["infrastructure_ok"] and not errors
                    row["status"] = status if row["policy_passed"] else "regression"
                    rows.append(row)
                    if not row["policy_passed"]:
                        failures.append(case)
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
    required_unqualified = [mode for mode in sorted(set(args.require_qualified_mode))
                            if not any(row["mode"] == mode for row in rows)
                            or any(row["status"] not in ("qualified", "control_passed")
                                   for row in rows if row["mode"] == mode)]
    code = int(bool(failures or required_unqualified or not cleaned or len(rows) != 48))
    summary = {"schema": 1, "authority": "diagnostic_only", "exit_code": code,
               "checks_passed": sum(row["policy_passed"] for row in rows), "cases": len(rows),
               "qualified_cases": sum(row["status"] == "qualified" for row in rows),
               "required_unqualified": required_unqualified,
               "controls_passed": sum(row["status"] == "control_passed" for row in rows),
               "cpu_cache_notices": sum(row["diagnostic_classification"] == "cpu_cache_size_unknown"
                                        for row in rows),
               "cleanup_complete": cleaned, "inputs_unchanged": unchanged,
               "input_provenance": {"files": provenance.files, "errors": provenance.errors},
               "fixtures": fixtures, "failures": failures, "rows": rows,
               "limitations": ["fixed single-threaded generated-code lifecycle fixtures",
                               "replay correction requires the prepared runtime's private ABI",
                               "not Java/Node JIT compilation or GC/deoptimization qualification",
                               "not isolate profile qualification or trusted retired instructions"]}
    (args.output_dir / "results.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({key: value for key, value in summary.items()
                      if key not in ("rows", "input_provenance", "fixtures")}))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
