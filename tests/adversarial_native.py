#!/usr/bin/env python3
"""Bounded hostile-target regressions; optional forgery is not an integrity pass."""
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk", type=Path, required=True)
    parser.add_argument("--client", type=Path, required=True)
    parser.add_argument("--runner-bin", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--demonstrate-report-forgery", action="store_true",
                        help="retain a same-UID forgery counterexample; cannot certify integrity")
    args = parser.parse_args()
    args.output_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    rows, limitations = [], []
    libc, previous = own_subreaper()
    try:
        with tempfile.TemporaryDirectory(prefix="native-adversarial-", dir=args.output_dir) as directory:
            work = Path(directory).resolve()
            executable = work / "native-adversarial"
            subprocess.run(["gcc", "-std=c17", "-O2", "-Wall", "-Wextra", "-Werror",
                            str(ROOT / "tests/adversarial_native.c"), "-o", str(executable)],
                           check=True)
            drrun = args.sdk.resolve() / "bin64/drrun"
            modes = ["normal", "fifo-stream", "symlink-stream", "hardlink-stream",
                     "execveat-fd", "execveat-memfd"]
            if args.demonstrate_report_forgery:
                modes.append("forge-records")
            for mode in modes:
                case = work / mode
                sentinel = work / (mode + "-sentinel")
                sentinel.write_bytes(b"untouched-sentinel\n")
                guest = [str(executable), mode, str(sentinel)]
                if mode.startswith("execveat-"):
                    native = run_bounded(guest, work / ("native-" + mode), 6000)
                    assert native["exit_code"] == 0 and not native["harness_timeout"], native
                report = case / "report.json"
                command = [str(args.runner_bin.resolve()), "run", "--drrun", str(drrun),
                           "--client", str(args.client.resolve()), "--report", str(report),
                           "--records-dir", str(case / "records"), "--timeout-ms", "6000",
                           "--", *guest]
                row = run_bounded(command, case, 6000)
                row.update(case=mode, passed=False)
                rows.append(row)
                if not report.is_file():
                    row["error"] = "supervisor report missing"
                    continue
                envelope = json.loads(report.read_text())
                row["report"] = envelope
                execution, measurement = envelope["execution"], envelope["measurement"]
                safe_cleanup = (not row["harness_timeout"] and row["harness_cleanup_complete"]
                                and execution.get("cleanup_complete") is True
                                and execution.get("records_cleanup_complete") is True)
                if mode == "forge-records":
                    root = next((process for process in measurement.get("processes", [])
                                 if process.get("pid") == execution.get("root_pid")), {})
                    observed = (row["exit_code"] == 0 and measurement.get("valid") is True
                                and root.get("instructions") == 1)
                    row["forgery_observed"] = observed
                    limitations.append({"case": mode, "forgery_observed": observed,
                                        "authority": measurement.get("authority"),
                                        "integrity_verified": False})
                    # The counterexample never counts as an attack prevented.
                    # Check only that the CLI explicitly refuses integrity claims.
                    row["passed"] = (safe_cleanup and measurement.get("authority") == "diagnostic_only"
                                     and envelope.get("security", {}).get("counter_integrity")
                                     == "application_mutable")
                elif mode == "normal":
                    row["passed"] = (safe_cleanup and row["exit_code"] == 0
                                     and measurement.get("valid") is True
                                     and measurement.get("instructions", 0) > 1000000)
                else:
                    row["passed"] = (safe_cleanup and row["exit_code"] == 1
                                     and execution.get("state") != "timed_out"
                                     and measurement.get("valid") is False
                                     and measurement.get("complete") is False
                                     and not any(key in measurement for key in TOTALS)
                                     and sentinel.read_bytes() == b"untouched-sentinel\n")
    finally:
        cleaned = cleanup_orphans()
        libc.prctl(36, previous, 0, 0, 0)
    summary = {"schema": 1, "authority": "diagnostic_only", "integrity_verified": False,
               "passed": sum(row["passed"] for row in rows), "cases": len(rows),
               "failures": [row["case"] for row in rows if not row["passed"]],
               "cleanup_complete": cleaned, "known_limitations": limitations, "rows": rows}
    (args.output_dir / "results.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({key: value for key, value in summary.items() if key != "rows"}))
    return 0 if cleaned and rows and all(row["passed"] for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
