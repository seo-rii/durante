#!/usr/bin/env python3
"""Fixed-count Java tiered regression against the prepared production runtime."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from concurrent_spawn import bounded_number, cleanup_orphans, own_subreaper
from runtime_compat import (InputProvenance, bounded_bytes, bounded_json,
                            executable_path, file_identity, run_process)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/runtime_fixtures/RuntimeProbe.java"
JAVA_FLAGS = ("-Xmx128m", "-Xss256k", "-XX:CompressedClassSpaceSize=32m",
              "-XX:ReservedCodeCacheSize=32m")
EXPECTED = "1249975000\n"
WALL_MS = 40000
CAMPAIGN_SECONDS = 600


def observation_plan(args, target):
    engine = [str(args.sdk / "bin64/drrun"), "-quiet", "-follow_children", "-disable_traces",
              "-vm_size", "32M", "-vmheap_size", "32M", "--", *target]
    rows = []
    for index in range(args.repeats + 1):
        mode = "native" if index == 0 else "engine-only"
        directory = args.output_dir / f"{index:03d}-{mode}"
        rows.append({"index": index, "mode": mode, "directory": str(directory),
                     "command": list(target) if index == 0 else list(engine),
                     "state": "incomplete", "passed": False, "root_pid": None,
                     "exit_code": None, "timeout": False, "cleanup_complete": None,
                     "stdout": None, "stderr": None, "hs_err_paths": [], "errors": [],
                     "stdout_path": str(directory / "stdout.txt"),
                     "stderr_path": str(directory / "stderr.txt")})
    return rows


def remaining_ms(deadline, limit=WALL_MS):
    return max(0, min(limit, int((deadline - time.monotonic()) * 1000)))


def run_observations(rows, prlimit, deadline):
    """Never replace a failed observation with a successful retry."""
    blocked = None
    for row in rows:
        timeout = remaining_ms(deadline)
        reason = blocked or ("campaign deadline expired" if timeout == 0 else None)
        if reason:
            row["errors"].append(reason)
            continue
        directory = Path(row["directory"])
        try:
            directory.mkdir(mode=0o700)
            row.update(run_process(row["command"], directory, timeout, prlimit))
            row["state"] = "complete"
            row.pop("accounting_ok", None)
            row["hs_err_paths"] = sorted(str(path) for path in directory.glob("hs_err_pid*.log"))
            row["functional_ok"] = (row["exit_code"] == 0 and row.get("stdout") == EXPECTED
                                    and row["cleanup_complete"] and not row["timeout"])
            if not row["functional_ok"]:
                row["errors"].append("expected exact fixture stdout and normal exit")
            if row.get("stderr"):
                row["errors"].append("unexpected application or runtime diagnostic")
            if row["hs_err_paths"]:
                row["errors"].append("JVM fatal-error report present")
            row["passed"] = row["functional_ok"] and row["infrastructure_ok"] and not row["errors"]
            if not row["cleanup_complete"]:
                blocked = "prior observation descendant cleanup incomplete"
            elif row["mode"] == "native" and not row["passed"]:
                blocked = "native control failed"
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            row["errors"].append(f"observation failed: {type(error).__name__}: {error}"[:600])
            blocked = "prior observation could not complete"


def observation_errors(rows, repeats):
    expected = [(0, "native"), *[(index, "engine-only") for index in range(1, repeats + 1)]]
    errors = []
    if [(row["index"], row["mode"]) for row in rows] != expected:
        errors.append("fixed observation plan is missing, duplicated or reordered")
    errors.extend(f"observation {row['index']} {row['mode']} {row['state']} failed"
                  for row in rows if row["state"] != "complete" or not row["passed"])
    return errors


def probe_and_compile(args, provenance, deadline, setup_rows):
    def execute(label, command, limit):
        timeout = remaining_ms(deadline, limit)
        if not timeout:
            raise ValueError("campaign deadline expired during setup")
        directory = args.output_dir / label
        directory.mkdir(mode=0o700)
        row = run_process(command, directory, timeout, args.prlimit)
        row.update(label=label, command=command, directory=str(directory),
                   hs_err_paths=sorted(str(path) for path in directory.glob("hs_err_pid*.log")))
        setup_rows.append(row)
        if row["exit_code"] != 0 or not row["infrastructure_ok"] or row["hs_err_paths"]:
            raise ValueError(label + " failed")
        return (row.get("stdout", "") + row.get("stderr", "")).strip()

    version = execute("version-java", [args.java, *JAVA_FLAGS, "-XX:+PrintCommandLineFlags", "-version"], 10000)
    match = re.search(r'\bversion "(17|21)(?:[.\"]|[-+])', version)
    if not match:
        raise ValueError("Java runtime must be a supported Java 17 or 21 installation")
    major = int(match[1])
    if "-XX:+UseG1GC" not in version:
        raise ValueError("selected Java installation did not select default G1 GC")
    compiler_flags = ["-J" + flag for flag in JAVA_FLAGS]
    compiler = execute("version-javac", [args.javac, *compiler_flags, "-version"], 10000)
    if not re.fullmatch(r"javac " + str(major) + r"(?:[.][0-9]+)*(?:[-+].*)?", compiler):
        raise ValueError("javac must match the selected Java major")
    classes = args.output_dir / "classes"
    classes.mkdir(mode=0o700)
    diagnostic = execute("compile-java", [args.javac, *compiler_flags, "-d", str(classes), str(FIXTURE)], WALL_MS)
    if diagnostic:
        raise ValueError("unexpected Java fixture compiler diagnostic")
    artifact = classes / "RuntimeProbe.class"
    provenance.files["generated:RuntimeProbe.class"] = {
        "path": str(artifact), "before": file_identity(artifact), "after": None, "unchanged": False}
    return {"java": {"path": args.java, "major": major, "version": version},
            "javac": {"path": args.javac, "version": compiler},
            "gc": "default G1", "jit": "default tiered"}


def check_production_runtime(args, provenance):
    # Compare the prepared release core with this checkout's production recipe.
    # The separate exit-fault SDK deliberately has no production manifest.
    spec = importlib.util.spec_from_file_location("tiered_runtime_recipe", ROOT / "toolkit/build_runtime.py")
    runtime = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runtime)
    manifest = bounded_json(args.sdk / runtime.MANIFEST)
    expected = runtime._inputs(bounded_bytes(runtime.PATCH), bounded_bytes(runtime.REPLAY_PATCH))
    core_hash = provenance.files["binary:core"]["before"]["sha256"]
    if (manifest.get("schema") != 1 or manifest.get("inputs") != expected
            or manifest.get("artifacts", {}).get("lib64/release/libdynamorio.so") != core_hash):
        raise ValueError("SDK release core does not match the current prepared production runtime")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("sdk", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--java", default="java")
    parser.add_argument("--javac", default="javac")
    parser.add_argument("--repeats", type=bounded_number(1, 30), default=30,
                        help="fixed number of engine observations (default: 30)")
    args = parser.parse_args(argv)
    args.sdk, args.output_dir = args.sdk.absolute(), args.output_dir.absolute()
    try:
        args.output_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    except OSError:
        parser.error("output directory must be fresh and writable")
    started = time.monotonic()
    deadline = started + CAMPAIGN_SECONDS
    args.java, args.javac = executable_path(args.java), executable_path(args.javac)
    args.prlimit = executable_path("prlimit")
    java_home = Path(args.java).resolve().parent.parent if args.java else None
    source_names = ("tests/java_tiered.py", "tests/runtime_compat.py", "tests/concurrent_spawn.py",
                    "tests/fixtures.py", "collect.py", "tests/runtime_fixtures/RuntimeProbe.java",
                    "toolkit/build_runtime.py", "toolkit/patches/dynamorio-vfork-lifetime.patch",
                    "toolkit/patches/dynamorio-code-replay.patch")
    inputs = {"source:" + name: ROOT / name for name in source_names}
    inputs.update({"binary:java": args.java, "binary:javac": args.javac,
                   "binary:libjvm": java_home / "lib/server/libjvm.so" if java_home else None,
                   "binary:modules": java_home / "lib/modules" if java_home else None,
                   "binary:drrun": args.sdk / "bin64/drrun",
                   "binary:core": args.sdk / "lib64/release/libdynamorio.so",
                   "binary:harness-python": Path(sys.executable).resolve(),
                   "binary:prlimit": args.prlimit, "runtime:manifest": args.sdk / ".durante-runtime.json"})
    provenance = InputProvenance(inputs)
    provenance.capture("before")
    target = [args.java, *JAVA_FLAGS, "-cp", str(args.output_dir / "classes"), "RuntimeProbe"]
    rows = observation_plan(args, target)
    failures, setup_rows, versions = [], [], {}
    owner, original_affinity = None, None
    affinity = {"supported": hasattr(os, "sched_getaffinity") and hasattr(os, "sched_setaffinity"),
                "original": None, "selected": None, "restored": True}
    cleaned = True
    try:
        if provenance.errors:
            raise ValueError("required Java or production-runtime input unavailable")
        if Path(args.javac).resolve().parent.parent != java_home:
            raise ValueError("java and javac must belong to the same JDK installation")
        check_production_runtime(args, provenance)
        owner = own_subreaper()
        if affinity["supported"]:
            original_affinity = os.sched_getaffinity(0)
            affinity.update(original=sorted(original_affinity), selected=sorted(original_affinity)[:4], restored=False)
            os.sched_setaffinity(0, affinity["selected"])
        versions = probe_and_compile(args, provenance, deadline, setup_rows)
        run_observations(rows, args.prlimit, deadline)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError, subprocess.SubprocessError) as error:
        failures.append(f"harness failure: {type(error).__name__}: {error}"[:600])
    finally:
        if owner is not None:
            try:
                cleaned = cleanup_orphans()
            except OSError as error:
                cleaned = False
                failures.append("cleanup inspection failed: " + str(error)[:300])
            if owner[0].prctl(36, owner[1], 0, 0, 0):
                cleaned = False
                failures.append("cannot restore harness process ownership")
        if original_affinity is not None:
            try:
                os.sched_setaffinity(0, original_affinity)
                affinity["restored"] = os.sched_getaffinity(0) == original_affinity
            except OSError as error:
                failures.append("cannot restore harness CPU affinity: " + str(error)[:300])
        provenance.capture("after")
    for row in rows:
        if row["state"] == "incomplete" and not row["errors"]:
            row["errors"].append("setup did not complete")
    failures.extend(observation_errors(rows, args.repeats))
    if not provenance.unchanged():
        failures.append("qualification inputs changed or could not be inspected")
    if not cleaned:
        failures.append("harness descendant cleanup incomplete")
    if not affinity["restored"]:
        failures.append("harness CPU affinity was not restored")
    code = int(bool(failures))
    summary = {"schema": 1, "authority": "diagnostic_only", "passed": code == 0, "exit_code": code,
               "engine_repeats": args.repeats, "planned_observations": len(rows),
               "completed_observations": sum(row["state"] == "complete" for row in rows),
               "checks_passed": sum(row["passed"] for row in rows), "rows": rows,
               "setup_rows": setup_rows, "versions": versions, "cpu_affinity": affinity,
               "bounds": {"address_space_bytes": 2147483648, "cpu_seconds": 40,
                          "file_bytes": 1048576, "core_bytes": 0, "wall_ms": WALL_MS,
                          "campaign_seconds": CAMPAIGN_SECONDS, "termination_cleanup_allowance_s": 8},
               "elapsed_s": round(time.monotonic() - started, 4), "cleanup_complete": cleaned,
               "inputs_unchanged": provenance.unchanged(), "failures": failures,
               "input_provenance": {"files": provenance.files, "errors": provenance.errors},
               "limitations": ["fixed arithmetic fixture and recorded Java installation only",
                               "default tiered execution does not assert compilation of a particular method",
                               "no counter or client qualification; no retry-to-green",
                               "bounded before/after hashes do not inventory every runtime dependency"]}
    try:
        (args.output_dir / "results.json").write_text(json.dumps(summary, indent=2) + "\n")
    except OSError as error:
        code = summary["exit_code"] = 1
        summary["passed"] = False
        failures.append("cannot publish results.json: " + str(error)[:300])
    print(json.dumps({key: value for key, value in summary.items()
                      if key not in ("rows", "setup_rows", "input_provenance")}))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
