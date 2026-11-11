#!/usr/bin/env python3
"""Reject executed compatibility ABIs without publishing partial process trees."""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile

from clone_clear_sighand import run_bounded
from concurrent_spawn import cleanup_orphans, own_subreaper

ROOT = Path(__file__).resolve().parents[1]
MODES = ("native-getpid", "native-fork", "native-enosys", "native-large-enosys",
         "unreachable-int80", "int80-getpid",
         "int80-fork", "x32-getpid", "x32-fork")
TOTALS = ("instructions", "rep_bytes", "rep_entries", "unsupported_rep_entries",
          "signals_observed", "processes", "segments", "process_count", "image_count")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk", type=Path, required=True)
    parser.add_argument("--client", type=Path, required=True)
    parser.add_argument("--runner-bin", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--diagnostic", action="store_true",
                        help="retain baseline failures without claiming qualification")
    args = parser.parse_args()
    args.output_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    libc, old = own_subreaper()
    rows = []
    try:
        with tempfile.TemporaryDirectory(prefix="compat-syscalls-", dir=args.output_dir) as name:
            work = Path(name).resolve()
            executable = work / "compat-syscalls"
            subprocess.run(["gcc", "-std=c17", "-O2", "-Wall", "-Wextra", "-Werror",
                            str(ROOT / "tests/compat_syscalls.c"), "-o", str(executable)],
                           check=True)
            drrun = args.sdk.resolve() / "bin64/drrun"
            no_rep = work / "drrun-no-rep"
            no_rep.write_text("#!/usr/bin/python3\nimport os,sys\n"
                              "args=sys.argv[1:]\nend=args.index('--')\n"
                              "args[:end]=[a for a in args[:end] if a!='-rep_bytes']\n"
                              f"path={str(drrun)!r}\nos.execv(path,[path,*args])\n")
            no_rep.chmod(0o700)
            native = {}
            for mode in MODES:
                control = run_bounded([str(executable), mode], work / ("native-" + mode), 2000)
                assert (control["exit_code"] == 0 and not control["harness_timeout"]
                        and control["harness_cleanup_complete"]), control
                native[mode] = control["stdout"]
            for rep in (False, True):
                for mode in MODES:
                    directory = work / (("rep-" if rep else "no-rep-") + mode)
                    report = directory / "report.json"
                    command = [str(args.runner_bin.resolve()), "run", "--drrun",
                               str(drrun if rep else no_rep), "--client", str(args.client.resolve()),
                               "--report", str(report), "--records-dir", str(directory / "records"),
                               "--timeout-ms", "3000", "--", str(executable), mode]
                    row = run_bounded(command, directory, 3000)
                    row.update(case=mode, rep_bytes_enabled=rep, passed=False)
                    rows.append(row)
                    if not report.is_file():
                        continue
                    envelope = json.loads(report.read_text())
                    execution, measurement = envelope["execution"], envelope["measurement"]
                    row["report"] = envelope
                    common = (not row["harness_timeout"] and row["harness_cleanup_complete"]
                              and execution.get("cleanup_complete") is True
                              and execution.get("records_cleanup_complete") is True
                              and execution.get("exit_code") == 0 and row["stdout"] == native[mode])
                    unsupported = mode.startswith(("int80-", "x32-"))
                    if unsupported:
                        row["passed"] = (common and row["exit_code"] == 1
                                         and measurement.get("valid") is False
                                         and measurement.get("complete") is False
                                         and not any(field in measurement for field in TOTALS))
                    else:
                        processes = 2 if mode == "native-fork" else 1
                        row["passed"] = (common and row["exit_code"] == 0
                                         and measurement.get("valid") is True
                                         and measurement.get("complete") is True
                                         and measurement.get("rep_bytes_enabled") is rep
                                         and measurement.get("process_count") == processes)
    finally:
        cleaned = cleanup_orphans()
        libc.prctl(36, old, 0, 0, 0)
    result = {"cases": len(rows), "passed": sum(row["passed"] for row in rows),
              "diagnostic": args.diagnostic, "cleanup_complete": cleaned,
              "failures": [row["case"] for row in rows if not row["passed"]],
              "x32_native_available": native.get("x32-getpid", "").strip() != "unavailable",
              "rows": rows}
    (args.output_dir / "results.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({key: value for key, value in result.items() if key != "rows"}))
    return 0 if args.diagnostic or (cleaned and rows and all(row["passed"] for row in rows)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
