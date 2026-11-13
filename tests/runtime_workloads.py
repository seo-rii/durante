#!/usr/bin/env python3
"""Require bounded Java, CPython and Node workloads to pass every execution mode."""
import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
import subprocess
import sys
import time

from concurrent_spawn import bounded_number, cleanup_orphans, collector, own_subreaper
from runtime_compat import (InputProvenance, bounded_bytes, bounded_json, collect_records,
                            executable_path, file_identity, run_process, telemetry_status)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/runtime_workload_fixtures"
RUNTIMES = ("java", "cpython", "node")
MODES = ("native", "engine-only", "client-rep-off", "supervisor")
JAVA_FLAGS = ("-Xmx64m", "-Xss256k", "-XX:CompressedClassSpaceSize=32m",
              "-XX:ReservedCodeCacheSize=32m", "-XX:ActiveProcessorCount=2", "-XX:+UseSerialGC")
NODE_FLAGS = ("--max-old-space-size=64", "--max-semi-space-size=2", "--expose-gc")
# Closed-form results are independent of the fixture loops and runtime scheduling.
OUTPUTS = {"threads": f"threads:{3 * 10000 * 10001 // 2}\n",
           "allocation-gc": f"allocation-gc:{16 * 128 * 255}:retained:{128 * 255}:observed\n",
           "processes": f"processes:{7 * 7 + 17 + 11 * 11 + 17}\n"}


@dataclass(frozen=True)
class Workload:
    name: str
    runtime: str
    command: list
    expected: str
    processes: int
    images: int
    minimum_threads: int = 1


def workload_cases(paths, classes):
    cases = []
    for runtime, executable in paths.items():
        if not executable:
            continue
        for workload in OUTPUTS:
            launches = ("POSIX_SPAWN", "FORK") if runtime == "java" and workload == "processes" else (None,)
            for launch in launches:
                name = runtime + "-" + workload + ("-" + launch.lower() if launch else "")
                if runtime == "java":
                    command = [executable, *JAVA_FLAGS]
                    if launch:
                        command.append("-Djdk.lang.Process.launchMechanism=" + launch)
                    command += ["-cp", str(classes), "RuntimeWorkload", workload]
                    if workload == "processes":
                        command.append(executable)
                elif runtime == "node":
                    command = [executable, *NODE_FLAGS, str(FIXTURES / "workload.js"), workload]
                else:
                    command = [executable, "-I", "-S", str(FIXTURES / "workload.py"), workload]
                processes = 3 if workload == "processes" else 1
                # Two children: fork image + target exec each; OpenJDK's selected
                # POSIX_SPAWN adds jspawnhelper's exec image for each child.
                images = 1 if processes == 1 else (7 if launch == "POSIX_SPAWN" else 5)
                cases.append(Workload(name, runtime, command, OUTPUTS[workload], processes, images,
                                      3 if workload == "threads" else 1))
    return cases


def same_json(left, right):
    # Python structural equality considers True == 1 and 1.0 == 1. A diagnostic
    # protocol comparison must preserve the integer and boolean field types.
    return json.dumps(left, sort_keys=True, allow_nan=False) == json.dumps(right, sort_keys=True, allow_nan=False)


def topology_errors(measurement, root_pid, workload):
    errors = []
    processes, segments = measurement.get("processes", []), measurement.get("segments", [])
    roots = [row for row in processes if row.get("pid") == root_pid]
    root_images = [row for row in segments if row.get("pid") == root_pid]
    if (type(root_pid) is not int or root_pid <= 0 or measurement.get("root_pid") != root_pid
            or len(roots) != 1 or len(root_images) != 1):
        return ["fixed workload root process/image is missing or changed"]
    root, root_image = roots[0], root_images[0]
    if (root.get("parent_pid") != 0 or root.get("parent_segment") != ""
            or root.get("images") != 1 or root.get("exit_code") != 0):
        errors.append("fixed workload root must have one normally exited initial image")
    threads = root_image.get("threads")
    if type(threads) is not int or threads < workload.minimum_threads:
        errors.append("root image did not account for the required worker threads")
    children = [row for row in processes if row.get("pid") != root_pid]
    if len(children) != workload.processes - 1:
        errors.append("fixed workload child process count differs")
    expected_images = (workload.images - 1) // (workload.processes - 1) if workload.processes > 1 else 0
    for child in children:
        child_images = [row for row in segments if row.get("pid") == child.get("pid")]
        if (child.get("parent_pid") != root_pid or child.get("parent_segment") != root_image.get("segment")
                or child.get("images") != expected_images or len(child_images) != expected_images
                or child.get("exit_code") != 0
                or sum(row.get("exit_code") == 0 for row in child_images) != 1):
            errors.append("child launch, image distribution or normal exit differs from the fixed topology")
    return errors


def measurement_errors(measurement, records, root_pid, workload, rep):
    errors = []
    reference = collector.collect(records, root_pid)
    if not same_json(measurement, reference):
        errors.append("Rust report differs from independently collected raw records")
    if (measurement.get("authority") != "diagnostic_only"
            or measurement.get("integrity_verified") is not False
            or not same_json(measurement.get("measurement_contract"), collector.measurement_contract())):
        errors.append("measurement diagnostic contract missing or promoted")
    if measurement.get("valid") is not True or measurement.get("complete") is not True:
        errors.append("complete process-tree measurement unavailable")
        if any(key in measurement for key in ("instructions", "rep_bytes", "processes", "segments")):
            errors.append("invalid measurement exposed partial totals")
        return errors
    if measurement.get("quality") not in ("uninterrupted", "signals_observed"):
        errors.append("unexpected accounting quality")
    if (measurement.get("process_count") != workload.processes
            or measurement.get("image_count") != workload.images):
        errors.append("process/image topology differs from the fixed launch oracle")
    errors.extend(topology_errors(measurement, root_pid, workload))
    if measurement.get("rep_bytes_enabled") is not rep:
        errors.append("REP mode differs from the selected execution")
    if type(measurement.get("instructions")) is not int or measurement["instructions"] <= 0:
        errors.append("instruction work is not a positive integer")
    if not rep and any(measurement.get(key) != 0 for key in (
            "rep_bytes", "rep_entries", "unsupported_rep_entries")):
        errors.append("disabled REP mode exposed telemetry")
    return errors


def envelope_errors(envelope):
    errors = []
    if (envelope.get("authority") != "diagnostic_only"
            or envelope.get("integrity_verified") is not False
            or not same_json(envelope.get("measurement_contract"), collector.measurement_contract())
            or envelope.get("security", {}).get("counter_integrity") != "application_mutable"
            or envelope.get("security", {}).get("isolation") != "external_required"):
        errors.append("supervisor diagnostic contract missing or promoted")
    execution = envelope.get("execution", {})
    if (execution.get("state") != "complete" or type(execution.get("exit_code")) is not int
            or execution["exit_code"] != 0
            or execution.get("cleanup_complete") is not True
            or execution.get("records_cleanup_complete") is not True):
        errors.append("supervisor target execution or cleanup incomplete")
    return errors


def run_case(args, workload, mode):
    directory = args.output_dir / (workload.name + "-" + mode)
    directory.mkdir(mode=0o700)
    records = directory / "records"
    command = workload.command
    if mode == "supervisor":
        command = [str(args.runner_bin), "run", "--drrun", str(args.drrun), "--client", str(args.client),
                   "--report", str(directory / "report.json"), "--records-dir", str(records),
                   "--timeout-ms", str(args.timeout_ms), "--", *command]
    elif mode != "native":
        prefix = [str(args.drrun), "-quiet", "-follow_children", "-disable_traces", "-vm_size", "32M",
                  "-vmheap_size", "32M", "-stderr_mask", "12"]
        if mode == "client-rep-off":
            records.mkdir(mode=0o700)
            prefix += ["-c", str(args.client), "-outdir", str(records)]
        command = [*prefix, "--", *command]
    row = run_process(command, directory, args.timeout_ms + (5000 if mode == "supervisor" else 0),
                      args.prlimit, cpu_seconds=90)
    row.update(workload=workload.name, runtime=workload.runtime, mode=mode, command=command,
               expected_stdout=workload.expected, expected_processes=workload.processes,
               expected_images=workload.images, telemetry_status="not-requested")
    errors = row["errors"]
    if row["exit_code"] != 0 or row.get("stdout") != workload.expected:
        errors.append("exact fixture output or exit oracle failed")
    if row.get("stderr"):
        errors.append("unexpected application or runtime diagnostic")
    if mode in ("client-rep-off", "supervisor"):
        rep = mode == "supervisor"
        try:
            if rep:
                envelope = bounded_json(directory / "report.json")
                row["report"] = envelope
                errors.extend(envelope_errors(envelope))
                root_pid = envelope.get("execution", {}).get("root_pid")
                measurement = envelope.get("measurement", {})
            else:
                root_pid = row["root_pid"]
                measurement = collect_records(args, directory, root_pid)
                if bounded_bytes(directory / "collector-stderr.txt"):
                    errors.append("unexpected collector diagnostic")
            row["measurement"] = measurement
            accounting = measurement_errors(measurement, records, root_pid, workload, rep)
            errors.extend(accounting)
            row["telemetry_status"] = telemetry_status(measurement, rep, not accounting)
        except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError) as error:
            errors.append("measurement unavailable: " + str(error)[:500])
    row["passed"] = row["infrastructure_ok"] and not errors
    return row


def matrix_errors(cases, rows):
    expected = {(case.name, mode) for case in cases for mode in MODES}
    observed = [(row["workload"], row["mode"]) for row in rows]
    errors = []
    if not expected or set(observed) != expected or len(observed) != len(expected):
        errors.append("execution matrix is missing cases or contains duplicates")
    errors += [row["workload"] + "/" + row["mode"] + " failed" for row in rows if not row["passed"]]
    return errors


def probe_and_compile(args, paths, provenance):
    versions = {}
    for runtime, executable in paths.items():
        directory = args.output_dir / ("version-" + runtime)
        directory.mkdir(mode=0o700)
        command = [executable, *JAVA_FLAGS, "-version"] if runtime == "java" else [executable, "--version"]
        row = run_process(command, directory, 10000, args.prlimit)
        if row["exit_code"] != 0 or not row["infrastructure_ok"]:
            raise ValueError("runtime version probe failed: " + runtime)
        version = (row.get("stdout", "") + row.get("stderr", "")).strip()
        if runtime == "java" and not re.search(r'\bversion "' + str(args.java_major) + r'[.\"]', version):
            raise ValueError("Java executable does not match required major " + str(args.java_major))
        versions[runtime] = {"path": executable, "version": version}
    classes = args.output_dir / "classes"
    if "java" in paths:
        classes.mkdir(mode=0o700)
        directory = args.output_dir / "version-javac"
        directory.mkdir(mode=0o700)
        compiler_flags = ["-J-Xmx128m", "-J-XX:CompressedClassSpaceSize=32m", "-J-XX:ActiveProcessorCount=2"]
        row = run_process([args.javac, *compiler_flags, "-version"], directory, 10000, args.prlimit)
        version = (row.get("stdout", "") + row.get("stderr", "")).strip()
        if (row["exit_code"] != 0 or not row["infrastructure_ok"]
                or not re.fullmatch(r"javac " + str(args.java_major) + r"(?:[.][0-9]+)*(?:[-+].*)?", version)):
            raise ValueError("javac does not match required Java major")
        directory = args.output_dir / "compile-java"
        directory.mkdir(mode=0o700)
        row = run_process([args.javac, *compiler_flags, "--release", str(args.java_major),
                           "-d", str(classes), str(FIXTURES / "RuntimeWorkload.java")],
                          directory, 30000, args.prlimit)
        if row["exit_code"] != 0 or not row["infrastructure_ok"] or row.get("stderr"):
            raise ValueError("bounded Java fixture compilation failed")
        artifact = classes / "RuntimeWorkload.class"
        provenance.files["generated:RuntimeWorkload.class"] = {
            "path": str(artifact), "before": file_identity(artifact), "after": None, "unchanged": False}
        versions["javac"] = {"path": args.javac, "version": version}
    return versions, classes


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("sdk", "client", "runner-bin", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--runtime", choices=RUNTIMES, action="append",
                        help="required runtime; repeat to select a subset, default: all three")
    for name, default in (("java", "java"), ("javac", "javac"), ("cpython", "python3"), ("node", "node")):
        parser.add_argument("--" + name, default=default)
    parser.add_argument("--java-major", type=int, choices=(17, 21), default=17)
    parser.add_argument("--timeout-ms", type=bounded_number(1000, 180000), default=120000)
    args = parser.parse_args(argv)
    for name in ("sdk", "client", "runner_bin", "output_dir"):
        setattr(args, name, getattr(args, name).absolute())
    try:
        args.output_dir.mkdir(parents=True, mode=0o700, exist_ok=False)
    except OSError:
        parser.error("output directory must be fresh and writable")
    selected = tuple(dict.fromkeys(args.runtime or RUNTIMES))
    paths = {runtime: executable_path(getattr(args, runtime)) for runtime in selected}
    args.javac = executable_path(args.javac) if "java" in selected else None
    args.prlimit = executable_path("prlimit")
    args.drrun = args.sdk / "bin64/drrun"
    source_names = ("tests/runtime_workloads.py", "tests/runtime_compat.py", "tests/concurrent_spawn.py",
                    "tests/fixtures.py", "collect.py", "tests/runtime_workload_fixtures/RuntimeWorkload.java",
                    "tests/runtime_workload_fixtures/workload.py", "tests/runtime_workload_fixtures/workload.js")
    inputs = {"source:" + name: ROOT / name for name in source_names}
    inputs.update({"binary:client": args.client, "binary:runner": args.runner_bin, "binary:drrun": args.drrun,
                   "binary:core": args.sdk / "lib64/release/libdynamorio.so", "binary:prlimit": args.prlimit,
                   "binary:harness-python": Path(sys.executable).resolve()})
    inputs.update({"binary:" + runtime: path for runtime, path in paths.items()})
    if "java" in selected:
        inputs["binary:javac"] = args.javac
        java_home = Path(paths["java"]).resolve().parent.parent if paths["java"] else None
        inputs["binary:jspawnhelper"] = java_home / "lib/jspawnhelper" if java_home else None
        inputs["binary:libjvm"] = java_home / "lib/server/libjvm.so" if java_home else None
    provenance = InputProvenance(inputs)
    provenance.capture("before")
    rows, failures, versions, cases = [], [], {}, []
    owner = None
    cleaned = True
    started = time.monotonic()
    try:
        if provenance.errors:
            raise ValueError("required runtime or qualification input unavailable before execution")
        owner = own_subreaper()
        versions, classes = probe_and_compile(args, paths, provenance)
        cases = workload_cases(paths, classes)
        for workload in cases:
            for mode in MODES:
                if time.monotonic() - started > 1200:
                    raise ValueError("workload matrix exceeded its 20-minute campaign deadline")
                rows.append(run_case(args, workload, mode))
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError, subprocess.SubprocessError) as error:
        failures.append(f"harness failure: {type(error).__name__}: {error}")
    finally:
        if owner is not None:
            libc, previous = owner
            try:
                cleaned = cleanup_orphans()
            except OSError:
                cleaned = False
            if libc.prctl(36, previous, 0, 0, 0):
                cleaned = False
        provenance.capture("after")
    if not provenance.unchanged():
        failures.append("qualification inputs changed or could not be inspected")
    if not cleaned:
        failures.append("harness descendant cleanup incomplete")
    failures.extend(matrix_errors(cases, rows))
    code = int(bool(failures))
    summary = {"schema": 1, "authority": "diagnostic_only", "integrity_verified": False,
               "passed": code == 0, "exit_code": code, "required_runtimes": selected,
               "java_major": args.java_major if "java" in selected else None,
               "workloads": len(cases), "observations": len(rows), "checks_passed": sum(row["passed"] for row in rows),
               "cleanup_complete": cleaned, "inputs_unchanged": provenance.unchanged(),
               "input_provenance": {"files": provenance.files, "errors": provenance.errors},
               "versions": versions, "failures": failures, "rows": rows,
               "limitations": ["fixed Linux x86-64 workloads, not arbitrary runtime compatibility",
                               "GC execution is observed, but JIT compilation/deoptimization is not asserted",
                               "runtime instruction totals vary with scheduling and collection; output/topology are exact",
                               "optional REP telemetry may be partial and signals remain visible",
                               "application-mutable diagnostic counts, not trusted CPU time or isolate qualification",
                               "before/after provenance is not hostile-host attestation or a complete runtime dependency inventory"]}
    try:
        (args.output_dir / "results.json").write_text(json.dumps(summary, indent=2) + "\n")
    except OSError as error:
        code = summary["exit_code"] = 1
        summary["passed"] = False
        summary["failures"].append("cannot publish results.json: " + str(error)[:300])
    print(json.dumps({key: value for key, value in summary.items() if key not in ("rows", "input_provenance")}))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
