#!/usr/bin/env python3
"""Bounded stock interpreter/JIT functional and process-tree qualification.

Native, engine-only, client REP-off and client REP-on are separate observations.
Known JVM client startup failures remain unqualified, not successful coverage.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time

from concurrent_spawn import bounded_number, cleanup_orphans, collector, own_subreaper

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/runtime_fixtures"
RUNTIMES = ("cpython", "pypy", "node", "java")
MODES = ("native", "engine-only", "rep-off", "rep-on")
MAX_CAPTURE = 65536
MAX_REPORT = 2 * 1024 * 1024


def bounded_bytes(path, maximum=MAX_CAPTURE):
    with path.open("rb") as stream:
        data = stream.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError("output/report exceeds inspection bound")
    return data


def bounded_json(path):
    return json.loads(bounded_bytes(path, MAX_REPORT))


def run_process(command, directory, timeout_ms):
    """One bounded process tree; always reap the harness's adopted descendants."""
    stdout, stderr = directory / "stdout.txt", directory / "stderr.txt"
    prlimit = shutil.which("prlimit")
    limited = [prlimit, "--as=2147483648", "--cpu=40", "--fsize=1048576",
               "--core=0", "--", *command]
    started = time.monotonic()
    timed_out = False
    with stdout.open("wb") as out, stderr.open("wb") as err:
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith("PYTHON") and key not in (
                           "NODE_OPTIONS", "NODE_V8_COVERAGE", "JAVA_TOOL_OPTIONS",
                           "_JAVA_OPTIONS", "JDK_JAVA_OPTIONS", "PYPYLOG", "PYPYJIT")}
        environment.update({"LANG": "C", "LC_ALL": "C"})
        process = subprocess.Popen(limited, cwd=directory, stdin=subprocess.DEVNULL,
                                   stdout=out, stderr=err, start_new_session=True,
                                   env=environment)
        pid = process.pid
        try:
            code = process.wait(timeout=timeout_ms / 1000)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(pid, signal.SIGTERM)
            try:
                code = process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(pid, signal.SIGKILL)
                code = process.wait(timeout=3)
        finally:
            if process.poll() is None:
                os.killpg(pid, signal.SIGKILL)
                process.wait(timeout=3)
            cleaned = cleanup_orphans()
    row = {"root_pid": pid, "exit_code": code, "timeout": timed_out,
           "cleanup_complete": cleaned, "elapsed_s": round(time.monotonic() - started, 4),
           "errors": [], "functional_ok": False, "accounting_ok": False}
    try:
        row["stdout"] = bounded_bytes(stdout).decode("utf-8")
        row["stderr"] = bounded_bytes(stderr).decode("utf-8", errors="replace")
    except (OSError, UnicodeError, ValueError) as error:
        row["errors"].append(str(error))
    if timed_out:
        row["errors"].append("outer wall deadline expired")
    if not cleaned:
        row["errors"].append("descendant cleanup failed")
    row["infrastructure_ok"] = cleaned and not timed_out and not row["errors"]
    return row


def collect_records(args, directory, root_pid):
    result = directory / "aggregate.json"
    error = directory / "collector-stderr.txt"
    with result.open("wb") as out, error.open("wb") as err:
        process = subprocess.run([shutil.which("prlimit"), "--as=536870912", "--cpu=5",
                                  "--fsize=2097152", "--core=0", "--", str(args.runner_bin),
                                  "collect", "--dir", str(directory / "records"),
                                  "--root-pid", str(root_pid)], stdout=out, stderr=err,
                                 timeout=10, check=False)
    aggregate = bounded_json(result)
    if process.returncode not in (0, 1):
        raise ValueError("collector did not return a protocol result")
    if process.returncode != (0 if aggregate.get("valid") else 1):
        raise ValueError("collector status disagrees with report validity")
    return aggregate


def inspect_accounting(measurement, records, root_pid, processes, rep_enabled):
    errors = []
    # A known runtime crash must never turn partial or stale records into a total.
    if not measurement.get("valid"):
        forbidden = ("instructions", "rep_bytes", "rep_entries", "unsupported_rep_entries",
                     "signals_observed", "processes", "segments", "process_count", "image_count")
        if measurement.get("complete") is not False or any(k in measurement for k in forbidden):
            return ["invalid accounting exposed partial totals"], True
        return ["measurement unavailable"], False
    reference = collector.collect(records, root_pid)
    if measurement != reference:
        errors.append("Rust and reference collectors disagree")
    if not measurement.get("complete") or measurement.get("quality") not in ("uninterrupted", "signals_observed"):
        errors.append("process-tree accounting was not complete")
    # Waiting on a subprocess delivers SIGCHLD normally. Keep the signal quality
    # explicit; complete lifecycle accounting does not mean exact retired work.
    if measurement.get("quality") == "signals_observed" and processes == 1:
        errors.append("unexpected signal in a single-process fixture")
    if measurement.get("rep_bytes_enabled") is not rep_enabled:
        errors.append("REP collection mode mismatch")
    if measurement.get("process_count") != processes:
        errors.append("process count differs from the fixed fixture tree")
    expected_images = 1 if processes == 1 else 3
    if measurement.get("image_count") != expected_images:
        errors.append("image count differs from the fixed parent/fork/exec tree")
    for key in ("instructions", "rep_bytes", "rep_entries", "unsupported_rep_entries", "signals_observed"):
        value = measurement.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            errors.append("invalid total counter")
            continue
        if value != sum(p[key] for p in measurement["processes"]) or value != sum(
                segment[key] for segment in measurement["segments"]):
            errors.append("process/image counter totals disagree")
    if measurement.get("instructions", 0) <= 0 or measurement.get("unsupported_rep_entries") != 0:
        errors.append("zero work or unsupported REP operation")
    return errors, bool(errors)


def run_case(args, work, name, runtime, target, expected, processes, index):
    rows = []
    for mode in MODES:
        directory = work / f"{index:02d}-{name}-{mode}"
        directory.mkdir(mode=0o700)
        records = directory / "records"
        command = target
        if mode != "native":
            command = [str(args.sdk / "bin64/drrun"), "-quiet", "-follow_children",
                       "-vm_size", "32M", "-vmheap_size", "32M"]
            if mode.startswith("rep-"):
                records.mkdir(mode=0o700)
                command += ["-c", str(args.client), "-outdir", str(records)]
                if mode == "rep-on":
                    command += ["-rep_bytes"]
            command += ["--", *target]
        row = run_process(command, directory, args.timeout_ms)
        row.update({"runtime": runtime, "fixture": name, "mode": mode,
                    "command": command, "diagnostic_integrity_failure": False})
        row["functional_ok"] = (row.get("stdout") == expected and row["exit_code"] == 0
                                and not row["timeout"] and row["cleanup_complete"])
        if not row["functional_ok"]:
            row["errors"].append("expected exact fixture stdout and normal exit")
        if mode.startswith("rep-"):
            try:
                measurement = collect_records(args, directory, row["root_pid"])
                row["measurement"] = measurement
                errors, integrity_failure = inspect_accounting(measurement, records,
                                                               row["root_pid"], processes,
                                                               mode == "rep-on")
                row["errors"].extend(errors)
                row["diagnostic_integrity_failure"] = integrity_failure
                row["accounting_ok"] = not errors
                if not row["functional_ok"] and measurement.get("valid"):
                    row["errors"].append("failed fixture produced a usable work total")
                    row["diagnostic_integrity_failure"] = True
            except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as error:
                row["errors"].append("accounting inspection failed: " + str(error))
                row["diagnostic_integrity_failure"] = True
        else:
            row["accounting_ok"] = True  # no counter was requested in this control
        row["passed"] = row["functional_ok"] and row["accounting_ok"] and not row["errors"]
        rows.append(row)
    return {"runtime": runtime, "fixture": name, "status": classify_case(runtime, rows),
            "qualification_scope": "this fixture and recorded installation only", "rows": rows}


def classify_case(runtime, rows):
    """Separate a reproduced startup incompatibility from arbitrary regressions."""
    controls = all(row["passed"] for row in rows[:2])
    all_passed = all(row["passed"] for row in rows)
    safe_diagnostic = all(row["infrastructure_ok"] and row["cleanup_complete"] and not row["diagnostic_integrity_failure"]
                          for row in rows)
    status = "passed" if all_passed else "regression"
    failed_clients = [row for row in rows[2:] if not row["passed"]]
    def documented_jvm_startup_failure(row):
        diagnostic = row.get("stdout", "") + row.get("stderr", "")
        return (not row["timeout"] and row["exit_code"] == -signal.SIGABRT
                and "A fatal error has been detected by the Java Runtime Environment" in diagnostic
                and "SIGSEGV" in diagnostic and "JRE version:  (17." in diagnostic
                and row.get("measurement", {}).get("valid") is False)
    if (runtime == "java" and controls and safe_diagnostic and failed_clients
            and all(documented_jvm_startup_failure(row) for row in failed_clients)):
        status = "known-unqualified"
    return status


def runtime_commands(args, work):
    paths = {"cpython": shutil.which(args.cpython), "pypy": shutil.which(args.pypy),
             "node": shutil.which(args.node), "java": shutil.which(args.java)}
    javac = shutil.which(args.javac)
    if paths["java"] and not javac:
        paths["java"] = None
    versions = {}
    for name, path in paths.items():
        if path:
            directory = work / ("version-" + name)
            directory.mkdir()
            version_flags = (["-Xmx128m", "-XX:CompressedClassSpaceSize=32m",
                              "-XX:ReservedCodeCacheSize=32m", "-version"]
                             if name == "java" else ["--version"])
            result = run_process([path, *version_flags], directory,
                                 min(args.timeout_ms, 10000))
            if result["exit_code"] != 0 or not result["infrastructure_ok"]:
                raise RuntimeError("runtime version probe failed: " + name)
            with Path(path).resolve().open("rb") as stream:
                executable_hash = hashlib.file_digest(stream, "sha256").hexdigest()
            versions[name] = {"path": str(Path(path).resolve()), "sha256": executable_hash,
                              "version": (result["stdout"] + result["stderr"]).strip()}
    if paths["java"]:
        compiler = work / "javac"
        compiler.mkdir()
        row = run_process([javac, "-J-Xmx128m", "-J-XX:CompressedClassSpaceSize=32m",
                           "-d", str(work), str(FIXTURES / "RuntimeProbe.java")], compiler,
                          args.timeout_ms)
        if row["exit_code"] != 0 or not row["infrastructure_ok"]:
            raise RuntimeError("bounded JVM fixture compilation failed: " + str(row))
    cases = []
    for name in ("cpython", "pypy"):
        if not paths[name]:
            continue
        for fixture, expected, processes in (
                ("arithmetic", "1249975000\n", 1),
                ("native_libraries", "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad\n30000\n", 1),
                ("subprocess_tree", "child:7\n", 2)):
            cases.append((f"{name}-{fixture}", name,
                          [paths[name], "-I", "-S", str(FIXTURES / (fixture + ".py"))],
                          expected, processes))
    if paths["node"]:
        for label, flags in (("jit", []), ("jitless", ["--jitless"])):
            cases.append(("node-" + label, "node", [paths["node"], *flags,
                          str(FIXTURES / "arithmetic.js")], "1571328\n", 1))
    if paths["java"]:
        for label, flags in (("tiered", []), ("interpreted", ["-Xint"])):
            cases.append(("java-" + label, "java", [paths["java"], "-Xmx128m", "-Xss256k",
                          "-XX:CompressedClassSpaceSize=32m", "-XX:ReservedCodeCacheSize=32m",
                          *flags, "-cp", str(work), "RuntimeProbe"], "1249975000\n", 1))
    return paths, versions, cases


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("sdk", "client", "runner-bin", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--require-runtime", choices=RUNTIMES, action="append", default=[])
    for name, default in (("cpython", "python3"), ("pypy", "pypy3"), ("node", "node"),
                          ("java", "java"), ("javac", "javac")):
        parser.add_argument("--" + name, default=default)
    parser.add_argument("--timeout-ms", type=bounded_number(1000, 60000), default=20000)
    parser.add_argument("--repeats", type=bounded_number(1, 5), default=1)
    args = parser.parse_args()
    if not shutil.which("prlimit"):
        parser.error("Linux prlimit is required")
    args.sdk, args.client, args.runner_bin, args.output_dir = [p.resolve() for p in (
        args.sdk, args.client, args.runner_bin, args.output_dir)]
    args.output_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    sources = [Path(__file__).resolve(), ROOT / "tests/test_runtime_compat.py",
               ROOT / "tests/concurrent_spawn.py",
               ROOT / "tests/fixtures.py", ROOT / "collect.py", *sorted(FIXTURES.glob("*"))]
    source_hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                     for p in sources if p.is_file()}
    binary_hashes = {}
    for name, path in (("core", args.sdk / "lib64/release/libdynamorio.so"),
                       ("client", args.client), ("runner", args.runner_bin)):
        with path.open("rb") as stream:
            binary_hashes[name] = hashlib.file_digest(stream, "sha256").hexdigest()
    libc, old_subreaper = own_subreaper()
    results, versions, missing = [], {}, []
    fatal = None
    try:
        paths, versions, cases = runtime_commands(args, args.output_dir)
        missing = [name for name, path in paths.items() if not path]
        for name in missing:
            results.append({"runtime": name, "fixture": None, "status": "skip",
                            "reason": "runtime executable (and javac for Java) unavailable"})
        for index in range(args.repeats):
            for case in cases:
                results.append(run_case(args, args.output_dir, *case, index))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        fatal = str(error)
    finally:
        cleaned = cleanup_orphans()
        libc.prctl(36, old_subreaper, 0, 0, 0)
    unchanged = all(hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == digest
                    for path, digest in source_hashes.items())
    required_missing = sorted(set(missing) & set(args.require_runtime))
    regressions = [r["fixture"] for r in results if r["status"] == "regression"]
    code = int(bool(fatal or not cleaned or not unchanged or required_missing or regressions))
    counts = {status: sum(row["status"] == status for row in results)
              for status in ("passed", "known-unqualified", "skip", "regression")}
    summary = {"schema": 1, "qualification_counts": counts, "authority": "diagnostic_only", "exit_code": code,
               "required_missing": required_missing, "missing_runtimes": missing,
               "regressions": regressions, "fatal_error": fatal,
               "harness_cleanup_complete": cleaned, "sources_unchanged": unchanged,
               "inputs": source_hashes, "binary_sha256": binary_hashes,
               "runtimes": versions, "results": results,
               "limitations": ["fixed fixtures only", "not production timing or trusted counters",
                               "known-unqualified is not passed compatibility",
                               "JIT-enabled mode does not prove a particular function compiled",
                               "SIGCHLD quality remains explicit in subprocess accounting"]}
    (args.output_dir / "results.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({k: v for k, v in summary.items() if k not in (
        "inputs", "binary_sha256", "results", "runtimes")}))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
