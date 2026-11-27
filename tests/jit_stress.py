#!/usr/bin/env python3
"""Run generated code that keeps changing while other threads run it.

The fixture (tests/jit_stress.c) has a patcher thread keep changing six
generated functions while worker threads call them. Each function has its own
version counter, so a call made while its function is stable must return that
version's result. A code cache that keeps running a stale copy returns an old
one. The modes change the code, or load the process, in different ways:

  inplace  patches on RWX pages; one instruction crosses a page boundary
  remap    replaces the page with a new mapping that holds the new code
  churn    inplace, with workers that are short-lived threads
  signal   inplace, with signals whose handler also calls the functions
  fork     inplace, with children forked while the code changes
  wx       pages that are RX and made RWX only around each patch

For each mode the harness runs a native control, then in each round the engine
alone and the supervisor with the client, each with the release and debug
cores. The engine runs without traces, as the supervisor runs it. Every case
must complete its rounds with no stale call and no failed child, and a mode's
own activity (threads, signals, forks) must have happened. --mode selects modes.
--latency-bound and --work-budget pass to the supervisor, whose entries then
charge the latency bound inline, with or without the live clock.
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

ENGINE_OPTIONS = ("-disable_traces",)
CORES = ("release", "debug")
ACTIVITY = {"churn": "threads_created", "signal": "signals", "fork": "forks"}
MODES = ("inplace", "remap", "churn", "signal", "fork", "wx")


def guest_errors(row, mode, rounds):
    guest = row["guest"]
    if row["harness_timeout"] or not row["harness_cleanup_complete"]:
        return ["harness deadline or cleanup failed"]
    if not isinstance(guest, dict) or guest.get("mode") != mode or guest.get("rounds") != rounds:
        return ["fixture did not complete its rounds"]
    errors = []
    if guest.get("patch_failed") != 0:
        errors.append("a code change failed")
    if guest.get("stale") != 0:
        errors.append(f'{guest.get("stale")} calls returned a stale result')
    if guest.get("bad_children") != 0:
        errors.append(f'{guest.get("bad_children")} forked children failed')
    if not guest.get("calls"):
        errors.append("workers made no calls")
    activity = ACTIVITY.get(mode)
    if activity and not guest.get(activity):
        errors.append(f"no {activity.replace('_', ' ')}")
    return errors


def fixture_args(args, executable, mode):
    return [str(executable), mode, str(args.rounds), str(args.threads)]


def run_native(args, work, executable, mode):
    row = run_bounded(fixture_args(args, executable, mode), work / f"{mode}-native",
                      args.timeout_ms)
    row.update(case=f"{mode}-native", mode=mode, errors=guest_errors(row, mode, args.rounds))
    if row["exit_code"] != 0:
        row["errors"].append("native control failed")
    row["passed"] = not row["errors"]
    return row


def run_engine(args, work, executable, mode, core, index):
    case = f"{mode}-{index:02d}-engine-{core}"
    command = [str(args.drrun), "-quiet", *(["-debug"] if core == "debug" else []),
               *ENGINE_OPTIONS, "-stderr_mask", "12", "--",
               *fixture_args(args, executable, mode)]
    row = run_bounded(command, work / case, args.timeout_ms)
    row.update(case=case, mode=mode, core=core, errors=guest_errors(row, mode, args.rounds))
    if row["exit_code"] != 0:
        row["errors"].append("fixture did not exit normally under the engine")
    row["passed"] = not row["errors"]
    return row


def run_supervised(args, work, executable, mode, core, index):
    case = f"{mode}-{index:02d}-supervisor-{core}"
    directory = work / case
    report, records = directory / "report.json", directory / "records"
    drrun = args.drrun if core == "release" else args.drrun_debug
    command = [str(args.runner_bin), "run", "--drrun", str(drrun), "--client", str(args.client),
               "--report", str(report), "--records-dir", str(records),
               "--timeout-ms", str(args.timeout_ms),
               *(["--latency-bound"] if args.latency_bound else []),
               *(["--work-budget", str(args.work_budget)] if args.work_budget else []), "--",
               *fixture_args(args, executable, mode)]
    row = run_bounded(command, directory, args.timeout_ms)
    row.update(case=case, mode=mode, core=core, errors=guest_errors(row, mode, args.rounds))
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
        bound = measurement.get("latency_bound")
        if args.latency_bound != (bound is not None) or (
                bound and not bound.get("cycles_x16", -1) >= 4 * measurement.get("instructions", 0)):
            errors.append("latency bound missing, unrequested or below issue cost")
    row["passed"] = not errors
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    for flag in ("sdk", "client", "runner-bin", "output-dir"):
        parser.add_argument("--" + flag, type=Path, required=True)
    parser.add_argument("--mode", action="append", choices=MODES, dest="modes")
    parser.add_argument("--repeats", type=bounded_number(1, 20), default=2)
    parser.add_argument("--rounds", type=bounded_number(100, 100000), default=3000)
    parser.add_argument("--threads", type=bounded_number(1, 16), default=2)
    parser.add_argument("--timeout-ms", type=bounded_number(1000, 300000), default=120000)
    parser.add_argument("--latency-bound", action="store_true")
    parser.add_argument("--work-budget", type=bounded_number(1, 2**63))
    args = parser.parse_args()
    modes = tuple(dict.fromkeys(args.modes or MODES))
    args.sdk, args.client, args.runner_bin = [p.resolve() for p in (args.sdk, args.client, args.runner_bin)]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="cases-", dir=args.output_dir.resolve()))
    source_paths = [ROOT / "tests/jit_stress.c", Path(__file__).resolve(),
                    ROOT / "tests/clone_clear_sighand.py", ROOT / "tests/concurrent_spawn.py"]
    source_hashes = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                     for path in source_paths}
    executable = work / "jit-stress"
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
    natives, rows = [], []
    try:
        for mode in modes:
            native = run_native(args, work, executable, mode)
            natives.append(native)
            if not native["passed"]:
                continue
            for index in range(args.repeats):
                for core in CORES:
                    rows.append(run_engine(args, work, executable, mode, core, index))
                    rows.append(run_supervised(args, work, executable, mode, core, index))
    finally:
        cleaned = cleanup_orphans()
        libc.prctl(36, old_subreaper, 0, 0, 0)
    sources_unchanged = all(hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == digest
                            for path, digest in source_hashes.items())
    failures = [row["case"] for row in (*natives, *rows) if not row["passed"]]
    expected = len(modes) * args.repeats * len(CORES) * 2
    success = (len(natives) == len(modes) and cleaned and sources_unchanged and not failures and
               len(rows) == expected)
    summary = {"exit_code": 0 if success else 1, "modes": list(modes), "rounds": args.rounds,
               "threads": args.threads, "latency_bound": args.latency_bound,
               "work_budget": args.work_budget, "cases": len(rows),
               "passed": sum(row["passed"] for row in rows), "failures": failures,
               "native_passed": all(row["passed"] for row in natives),
               "harness_cleanup_complete": cleaned, "source_unchanged": sources_unchanged,
               "inputs": source_hashes, "binary_sha256": binary_hashes,
               "natives": natives, "rows": rows}
    (args.output_dir / "results.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({key: value for key, value in summary.items()
                      if key not in ("rows", "natives")}))
    return summary["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
