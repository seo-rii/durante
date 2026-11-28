#!/usr/bin/env python3
"""Attack suite for the -protect_meter / --protect-meter integrity mode.

Each fixture (tests/meter_integrity.c) does real work and then tries one route
to change Durante's counts: an ordinary store, a read-modify-write, a REP/SIMD
or stack-pivot store, a code-cache patch, a system call that writes protected
memory, or a rewrite of the client's record stream. It finds its targets the
way an attacker would, through /proc/self/maps and /proc/self/fd.

Every mode runs twice through `durante run` on the prepared runtime: once
without --protect-meter and once with it.

  - An "open" route is reachable today. Without the mode the fixture prints
    {"reached": true, ...} and the run is still valid; with the mode the store
    or system call is stopped before it executes, the process is terminated,
    and the measurement is reported invalid with an integrity_violation reason
    naming the vector. The attack never prints its result.
  - A "dr_defended" route (mmap MAP_FIXED, mprotect over the runtime) the stock
    runtime already refuses, so it does not reach even without the mode; with
    the mode it is additionally reported as an explicit integrity violation.
  - The honest control must pass under the mode with an instruction count
    exactly equal to the count without it.

Pass for the suite means every mode matched its expectation.
"""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile

from clone_clear_sighand import run_bounded
from concurrent_spawn import cleanup_orphans, own_subreaper

ROOT = Path(__file__).resolve().parents[1]
TOTALS = ("instructions", "rep_bytes", "rep_entries", "unsupported_rep_entries",
          "signals_observed", "processes", "segments", "process_count", "image_count")

# mode -> (classification, expected integrity vector under the mode)
OPEN = "open"
DEFENDED = "dr_defended"
MODES = {
    "honest": ("control", None),
    "store-plain": (OPEN, "memory_store"),
    "store-rmw": (OPEN, "memory_store"),
    "store-rep": (OPEN, "memory_store"),
    "store-simd": (OPEN, "memory_store"),
    "store-stack": (OPEN, "memory_store"),
    "store-codecache": (OPEN, "memory_store"),
    "syscall-read": (OPEN, "syscall_output"),
    "syscall-procmem": (OPEN, "proc_write"),
    "syscall-processvm": (OPEN, "process_vm_writev"),
    "syscall-madvise": (OPEN, "memory_map"),
    "syscall-mmap-fixed": (DEFENDED, "memory_map"),
    "syscall-mprotect": (DEFENDED, "memory_map"),
    "file-records": (OPEN, "protected_file"),
    "file-report": (OPEN, "protected_file"),
}
INTEGRITY_PREFIX = "integrity violation: "


def run_case(args, work, mode, protect):
    tag = f"{mode}-{'protect' if protect else 'default'}"
    case = work / tag
    report = case / "report.json"
    command = [str(args.runner_bin.resolve()), "run", "--drrun",
               str((args.sdk.resolve() / "bin64/drrun")), "--client",
               str(args.client.resolve()), "--report", str(report),
               "--records-dir", str(case / "records"), "--timeout-ms", "20000"]
    if protect:
        command.append("--protect-meter")
    command += ["--", str(work / "meter-attack"), mode]
    if mode == "file-report":
        # The strongest case: the submission knows the report's absolute path.
        command.append(str(report))
    row = run_bounded(command, case, 20000)
    envelope = json.loads(report.read_text()) if report.is_file() else None
    reached = isinstance(row.get("guest"), dict) and row["guest"].get("reached") is True
    return row, envelope, reached


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk", type=Path, required=True,
                        help="prepared runtime SDK that supports -durante_window")
    parser.add_argument("--client", type=Path, required=True)
    parser.add_argument("--runner-bin", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--mode", action="append", help="limit to these modes")
    args = parser.parse_args()
    args.output_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    selected = args.mode or list(MODES)

    rows = []
    libc, previous = own_subreaper()
    try:
        with tempfile.TemporaryDirectory(prefix="meter-integrity-", dir=args.output_dir) as directory:
            work = Path(directory).resolve()
            subprocess.run(["gcc", "-std=c17", "-O2", "-Wall", "-Wextra", "-Werror",
                            str(ROOT / "tests/meter_integrity.c"), "-o", str(work / "meter-attack")],
                           check=True)
            honest_counts = {}
            for mode in selected:
                kind, vector = MODES[mode]
                row = {"mode": mode, "classification": kind, "passed": False}
                default_row, default_env, default_reached = run_case(args, work, mode, False)
                protect_row, protect_env, protect_reached = run_case(args, work, mode, True)
                row["default"] = {"exit": default_row["exit_code"], "reached": default_reached,
                                  "state": (default_env or {}).get("execution", {}).get("state"),
                                  "valid": (default_env or {}).get("measurement", {}).get("valid"),
                                  "reason": (default_env or {}).get("measurement", {}).get("reason")}
                protect_meas = (protect_env or {}).get("measurement", {})
                row["protect"] = {"exit": protect_row["exit_code"], "reached": protect_reached,
                                  "state": (protect_env or {}).get("execution", {}).get("state"),
                                  "valid": protect_meas.get("valid"),
                                  "reason": protect_meas.get("reason")}
                safe = (not default_row["harness_timeout"] and not protect_row["harness_timeout"])
                if kind == "control":
                    dmeas = (default_env or {}).get("measurement", {})
                    equal = (dmeas.get("valid") is True and protect_meas.get("valid") is True
                             and dmeas.get("instructions") == protect_meas.get("instructions")
                             and dmeas.get("instructions", 0) > 1000000)
                    honest_counts = {"default": dmeas.get("instructions"),
                                     "protect": protect_meas.get("instructions")}
                    row["counts"] = honest_counts
                    protection = (protect_env or {}).get("meter_protection") == {"version": 1}
                    row["passed"] = safe and equal and protection and default_reached and protect_reached
                else:
                    # Under the mode the attempt is an integrity violation that
                    # names the expected vector and exposes no usable totals.
                    reason = protect_meas.get("reason") or ""
                    blocked = (protect_meas.get("valid") is False
                               and reason == INTEGRITY_PREFIX + vector
                               and not any(k in protect_meas for k in TOTALS)
                               and not protect_reached)
                    if kind == OPEN:
                        # The route is reachable without the mode.
                        today = (row["default"]["valid"] is True and default_reached)
                    else:
                        # The stock runtime already refuses it; it must at least
                        # not have reached, and the run stays usable.
                        today = (row["default"]["valid"] is True and not default_reached)
                    row["demonstrated_today"] = today
                    row["blocked_by_mode"] = blocked
                    row["passed"] = safe and today and blocked
                rows.append(row)
    finally:
        cleaned = cleanup_orphans()
        libc.prctl(36, previous, 0, 0, 0)

    summary = {"schema": 1, "authority": "diagnostic_only", "integrity_verified": False,
               "passed": sum(r["passed"] for r in rows), "cases": len(rows),
               "failures": [r["mode"] for r in rows if not r["passed"]],
               "cleanup_complete": cleaned, "rows": rows}
    (args.output_dir / "results.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({k: v for k, v in summary.items() if k != "rows"}))
    return 0 if cleaned and rows and all(r["passed"] for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
