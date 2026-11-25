#!/usr/bin/env python3
"""Translate faults in generated code that a write to its page has just flushed.

The fixture (tests/flushed_fault.c) takes a fault, as at a JIT's safepoint poll,
inside a block that another thread's write has just flushed, and checks the pc
its handler is given. A runtime that rebuilt the flushed block against the
page's new self-modifying state could not translate the fault and reported a
zero pc, which aborts HotSpot (BUG-006, docs/JVM_SAFEPOINT.md).

Each round runs the fixture natively, under the engine alone with the release
and debug cores (with thresholds that switch the page to sandboxing at every
write, so that each write is such a flush), and under the supervisor with the
client and default options. Every fault must be reported at the poll.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import tempfile

from concurrent_spawn import ROOT, bounded_number, cleanup_orphans, own_subreaper
from clone_clear_sighand import run_bounded

ENGINE_OPTIONS = ("-ro2sandbox_threshold", "1", "-sandbox2ro_threshold", "2")
CORES = ("release", "debug")


def guest_errors(row, rounds):
    guest = row["guest"]
    if row["harness_timeout"] or not row["harness_cleanup_complete"]:
        return ["harness deadline or cleanup failed"]
    if not isinstance(guest, dict) or guest.get("faults", 0) < rounds:
        return ["fixture did not complete its rounds"]
    if guest.get("wrong_pc") != 0:
        return [f'{guest["wrong_pc"]} faults reported away from the poll']
    return []


def run_native(args, work, executable):
    row = run_bounded([str(executable), str(args.rounds)], work / "native", args.timeout_ms)
    row.update(case="native", errors=guest_errors(row, args.rounds))
    if row["exit_code"] != 0:
        row["errors"].append("native control failed")
    row["passed"] = not row["errors"]
    return row


def run_engine(args, work, executable, core, index):
    case = f"{index:02d}-engine-{core}"
    command = [str(args.drrun), "-quiet", *(["-debug"] if core == "debug" else []),
               *ENGINE_OPTIONS, "-stderr_mask", "12", "--", str(executable), str(args.rounds)]
    row = run_bounded(command, work / case, args.timeout_ms)
    row.update(case=case, core=core, errors=guest_errors(row, args.rounds))
    if row["exit_code"] != 0:
        row["errors"].append("fixture did not exit normally under the engine")
    row["passed"] = not row["errors"]
    return row


def run_supervised(args, work, executable, core, index):
    case = f"{index:02d}-supervisor-{core}"
    directory = work / case
    report, records = directory / "report.json", directory / "records"
    drrun = args.drrun if core == "release" else args.drrun_debug
    command = [str(args.runner_bin), "run", "--drrun", str(drrun), "--client", str(args.client),
               "--report", str(report), "--records-dir", str(records),
               "--timeout-ms", str(args.timeout_ms), "--", str(executable), str(args.rounds)]
    row = run_bounded(command, directory, args.timeout_ms)
    row.update(case=case, core=core, errors=guest_errors(row, args.rounds))
    errors = row["errors"]
    if not report.is_file():
        errors.append("missing supervisor report")
    else:
        envelope = json.loads(report.read_text())
        execution, measurement = envelope["execution"], envelope["measurement"]
        row["execution"], row["measurement"] = execution, measurement
        if execution["exit_code"] != 0 or execution["state"] != "complete":
            errors.append("fixture or supervisor did not complete normally")
        if not execution["cleanup_complete"] or not execution["records_cleanup_complete"]:
            errors.append("supervisor cleanup failed")
        if measurement.get("valid") is not True or measurement.get("complete") is not True:
            errors.append("complete measurement unavailable")
    row["passed"] = not errors
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("sdk", "client", "runner-bin", "output-dir"):
        parser.add_argument("--" + flag, type=Path, required=True)
    parser.add_argument("--repeats", type=bounded_number(1, 20), default=3)
    parser.add_argument("--rounds", type=bounded_number(10, 2000), default=200)
    parser.add_argument("--timeout-ms", type=bounded_number(1000, 120000), default=60000)
    args = parser.parse_args()
    args.sdk, args.client, args.runner_bin = [p.resolve() for p in (args.sdk, args.client, args.runner_bin)]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="cases-", dir=args.output_dir.resolve()))
    source_paths = [ROOT / "tests/flushed_fault.c", Path(__file__).resolve(),
                    ROOT / "tests/clone_clear_sighand.py", ROOT / "tests/concurrent_spawn.py"]
    source_hashes = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                     for path in source_paths}
    executable = work / "flushed-fault"
    subprocess.run(["gcc", "-std=c17", "-O2", "-Wall", "-Wextra", "-Werror", "-pthread",
                    str(source_paths[0]), "-o", str(executable)], check=True, umask=0o022)
    args.drrun = args.sdk / "bin64/drrun"
    args.drrun_debug = work / "drrun-debug"
    args.drrun_debug.write_text(f'#!/bin/sh\nexec {shlex.quote(str(args.drrun))} -debug "$@"\n')
    args.drrun_debug.chmod(0o755)
    binary_hashes = {}
    for label, path in (("release_core", args.sdk / "lib64/release/libdynamorio.so"),
                        ("debug_core", args.sdk / "lib64/debug/libdynamorio.so"),
                        ("client", args.client), ("runner", args.runner_bin)):
        with path.open("rb") as stream:
            binary_hashes[label] = hashlib.file_digest(stream, "sha256").hexdigest()
    libc, old_subreaper = own_subreaper()
    rows = []
    try:
        native = run_native(args, work, executable)
        if native["passed"]:
            for index in range(args.repeats):
                for core in CORES:
                    rows.append(run_engine(args, work, executable, core, index))
                    rows.append(run_supervised(args, work, executable, core, index))
    finally:
        cleaned = cleanup_orphans()
        libc.prctl(36, old_subreaper, 0, 0, 0)
    sources_unchanged = all(hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == digest
                            for path, digest in source_hashes.items())
    failures = [row["case"] for row in rows if not row["passed"]]
    success = (native["passed"] and cleaned and sources_unchanged and not failures and
               len(rows) == args.repeats * len(CORES) * 2)
    summary = {"exit_code": 0 if success else 1, "rounds": args.rounds, "cases": len(rows),
               "passed": len(rows) - len(failures), "failures": failures,
               "native_passed": native["passed"], "harness_cleanup_complete": cleaned,
               "source_unchanged": sources_unchanged, "inputs": source_hashes,
               "binary_sha256": binary_hashes, "native": native, "rows": rows}
    (args.output_dir / "results.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({key: value for key, value in summary.items() if key not in ("rows", "native")}))
    return summary["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
