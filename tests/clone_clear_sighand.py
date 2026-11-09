#!/usr/bin/env python3
"""Exercise real clone3 CLEAR_SIGHAND, without libc's clone fallback."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import tempfile
import time

from concurrent_spawn import (ROOT, bounded_number, cleanup_orphans, collector,
                              own_subreaper, read_excerpt)

MODES = ("normal", "failed-exec", "nonvm")
TOTALS = ("instructions", "rep_bytes", "rep_entries", "unsupported_rep_entries",
          "signals_observed", "processes", "segments", "process_count", "image_count")


def run_bounded(command, directory, timeout_ms):
    directory.mkdir()
    started = time.monotonic()
    timed_out = False
    with (directory / "stdout.txt").open("wb") as out, (directory / "stderr.txt").open("wb") as err:
        process = subprocess.Popen(command, stdout=out, stderr=err)
        try:
            code = process.wait(timeout=timeout_ms / 1000 + 5)
        except subprocess.TimeoutExpired:
            timed_out = True
            process.send_signal(signal.SIGTERM)
            try:
                code = process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                process.kill()
                code = process.wait(timeout=4)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=4)
            cleaned = cleanup_orphans()
    row = {"exit_code": code, "elapsed_s": round(time.monotonic() - started, 4),
           "harness_timeout": timed_out, "harness_cleanup_complete": cleaned,
           "stdout": read_excerpt(directory / "stdout.txt"),
           "stderr": read_excerpt(directory / "stderr.txt")}
    try:
        row["guest"] = json.loads(row["stdout"])
    except json.JSONDecodeError:
        row["guest"] = None
    return row


def run_instrumented(args, work, executable, mode, index, native):
    directory = work / f"{index:02d}-{mode}"
    report, records = directory / "report.json", directory / "records"
    command = [str(args.runner_bin), "run", "--drrun", str(args.drrun),
               "--client", str(args.client), "--report", str(report),
               "--records-dir", str(records), "--timeout-ms", str(args.timeout_ms),
               "--", str(executable), mode, str(work / "missing-executable")]
    row = run_bounded(command, directory, args.timeout_ms)
    row.update(case=directory.name, mode=mode, errors=[])
    errors = row["errors"]
    if row["harness_timeout"] or not row["harness_cleanup_complete"]:
        errors.append("harness deadline or cleanup failed")
    if not report.is_file():
        errors.append("missing supervisor report")
        row["passed"] = False
        return row
    envelope = json.loads(report.read_text())
    execution, measurement = envelope["execution"], envelope["measurement"]
    row["execution"], row["measurement"] = execution, measurement
    if not execution["cleanup_complete"] or not execution["records_cleanup_complete"]:
        errors.append("supervisor cleanup failed")
    events = []
    for path in sorted(records.glob("process-*.jsonl")):
        for line in path.read_text().splitlines():
            event = json.loads(line)
            if event.get("event") == "spawn":
                events.append(event)
    row["clone3_events"] = events
    supported = mode != "nonvm"
    if len(events) != 1 or events[0].get("syscall") != 435 or events[0].get("supported") is not supported:
        errors.append("missing real clone3 event or incorrect support classification")
    if not supported:
        # Fork-style CLEAR_SIGHAND is deliberately unsupported by this runtime
        # patch. Guest execution still happens; never publish partial counters.
        if measurement.get("valid") is not False or measurement.get("complete") is not False or any(
                field in measurement for field in TOTALS):
            errors.append("unsupported clone exposed a usable or partial measurement")
    else:
        if row["exit_code"] != 0 or execution["exit_code"] != 0 or execution["state"] != "complete":
            errors.append("fixture or supervisor did not complete normally")
        if row["guest"] != native:
            errors.append("guest signal state differs from native clone3")
        if measurement.get("valid") is not True or measurement.get("complete") is not True:
            errors.append("complete measurement unavailable")
        else:
            if measurement != collector.collect(records, execution["root_pid"]):
                errors.append("Rust aggregate differs from the Python reference")
            if measurement["process_count"] != 2 or measurement["image_count"] != 2:
                errors.append("expected exactly parent and child, with no successful exec")
            children = [p for p in measurement["processes"] if p["pid"] != execution["root_pid"]]
            if len(children) != 1 or children[0]["exit_code"] != (127 if mode == "failed-exec" else 0):
                errors.append("child exit status mismatch")
    row["passed"] = not errors
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("sdk", "client", "runner-bin", "output-dir"):
        parser.add_argument("--" + flag, type=Path, required=True)
    parser.add_argument("--repeats", type=bounded_number(1, 20), default=3)
    parser.add_argument("--timeout-ms", type=bounded_number(1000, 60000), default=20000)
    parser.add_argument("--engine-debug", action="store_true")
    parser.add_argument("--modes", nargs="+", choices=MODES, default=list(MODES))
    parser.add_argument("--require-clone3", action="store_true",
                        help="fail if native clone3 is unavailable or denied, as required in CI")
    args = parser.parse_args()
    args.sdk, args.client, args.runner_bin = [p.resolve() for p in (args.sdk, args.client, args.runner_bin)]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="cases-", dir=args.output_dir.resolve()))
    source_paths = [ROOT / "tests/clone_clear_sighand.c", Path(__file__).resolve(),
                    ROOT / "tests/concurrent_spawn.py", ROOT / "collect.py"]
    source_hashes = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                     for path in source_paths}
    executable = work / "clone-clear-sighand"
    subprocess.run(["gcc", "-std=c17", "-O2", "-Wall", "-Wextra", "-Werror",
                    str(source_paths[0]), "-o", str(executable)], check=True, umask=0o022)
    args.drrun = args.sdk / "bin64/drrun"
    if args.engine_debug:
        wrapper = work / "drrun-debug"
        wrapper.write_text(f'#!/bin/sh\nexec {shlex.quote(str(args.drrun))} -debug "$@"\n')
        wrapper.chmod(0o755)
        args.drrun = wrapper
    libc, old_subreaper = own_subreaper()
    rows, native, binary_hashes = [], {}, {}
    skipped, reason = False, None
    try:
        for mode in args.modes:
            row = run_bounded([str(executable), mode, str(work / "missing-executable")],
                              work / ("native-" + mode), args.timeout_ms)
            native[mode] = row
            if len(native) == 1 and row["exit_code"] == 77 and row["guest"] and row["guest"].get(
                    "clone3_errno") in (1, 38) and row["harness_cleanup_complete"] and not row["harness_timeout"]:
                skipped, reason = True, f'native clone3 errno {row["guest"]["clone3_errno"]}'
                break
            if row["exit_code"] != 0 or not row["guest"] or not row["guest"].get("ok") or not row[
                    "harness_cleanup_complete"] or row["harness_timeout"]:
                reason = f"native {mode} control failed"
                break
        if not skipped and reason is None:
            core = args.sdk / "lib64" / ("debug" if args.engine_debug else "release") / "libdynamorio.so"
            for label, path in (("core", core), ("client", args.client), ("runner", args.runner_bin)):
                with path.open("rb") as stream:
                    binary_hashes[label] = hashlib.file_digest(stream, "sha256").hexdigest()
            for index in range(args.repeats):
                for mode in args.modes:
                    rows.append(run_instrumented(args, work, executable, mode, index, native[mode]["guest"]))
    finally:
        cleaned = cleanup_orphans()
        libc.prctl(36, old_subreaper, 0, 0, 0)
    sources_unchanged = all(hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == digest
                            for path, digest in source_hashes.items())
    failures = [row["case"] for row in rows if not row["passed"]]
    passed = len(rows) - len(failures)
    success = cleaned and sources_unchanged and (skipped and not args.require_clone3 or
              not skipped and reason is None and len(rows) == args.repeats * len(args.modes) and not failures)
    summary = {"exit_code": 0 if success else 1, "skipped": skipped, "reason": reason,
               "cases": len(rows), "passed": passed, "failures": failures,
               "require_clone3": args.require_clone3, "engine_debug": args.engine_debug,
               "harness_cleanup_complete": cleaned, "source_unchanged": sources_unchanged,
               "inputs": source_hashes, "binary_sha256": binary_hashes, "native": native, "rows": rows}
    (args.output_dir / "results.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({key: value for key, value in summary.items() if key not in ("rows", "native")}))
    return summary["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
