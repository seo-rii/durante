#!/usr/bin/env python3
"""Compare close_range semantics and ordinary Python subprocesses with native controls."""
import argparse
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile

from clone_clear_sighand import run_bounded
from concurrent_spawn import cleanup_orphans, own_subreaper

ROOT = Path(__file__).resolve().parents[1]
MODES = ("all", "empty", "invalid", "cloexec", "unshare-thread", "signalfd")
PYTHON = {
    "default": 'import subprocess; assert subprocess.run(["/bin/true"]).returncode == 0; print("ok")',
    "pipes": 'import subprocess; r=subprocess.run(["/bin/sh","-c","cat; printf durante-stderr >&2"],input="hello",text=True,capture_output=True); assert (r.returncode,r.stdout)==(0,"hello") and "durante-stderr" in r.stderr; print("ok")',
    "pass-fds": 'import os,subprocess,sys; fd=os.open("/dev/null",os.O_RDONLY); r=subprocess.run([sys.executable,"-c",f"import os; os.fstat({fd})"],pass_fds=(fd,)); assert r.returncode==0; print("ok")',
    "failed-exec": 'import subprocess\ntry: subprocess.run(["/durante-nonexistent-subprocess"])\nexcept FileNotFoundError: print("ok")\nelse: raise AssertionError("missing exec succeeded")',
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk", type=Path, required=True)
    parser.add_argument("--client", type=Path, required=True)
    parser.add_argument("--runner-bin", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--engine-debug", action="store_true")
    parser.add_argument("--repeats", type=int, default=2)
    args = parser.parse_args()
    if not 1 <= args.repeats <= 20:
        parser.error("repeats must be in 1..20")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    # Debug instrumentation makes nested interpreter startup substantially slower.
    timeout_ms = 60000 if args.engine_debug else 20000
    libc, previous = own_subreaper()
    try:
        with tempfile.TemporaryDirectory(prefix="close-range-", dir=args.output_dir) as directory:
            work = Path(directory).resolve()
            executable = work / "close-range"
            subprocess.run(["gcc", "-std=c17", "-O2", "-Wall", "-Wextra", "-Werror", "-pthread",
                            str(ROOT / "tests/close_range.c"), "-o", str(executable)], check=True)
            drrun = args.sdk.resolve() / "bin64/drrun"
            if args.engine_debug:
                wrapper = work / "drrun-debug"
                wrapper.write_text(f'#!/bin/sh\nexec {shlex.quote(str(drrun))} -debug "$@"\n')
                wrapper.chmod(0o755)
                drrun = wrapper
            cases = [(mode, [str(executable), mode]) for mode in MODES]
            # Debug-core diagnostics are intentionally inherited by child stderr.
            # Release must preserve the exact application bytes on both pipes.
            cases += [("python-" + name, [sys.executable, "-c", code if args.engine_debug else
                       code.replace('"durante-stderr" in r.stderr', 'r.stderr == "durante-stderr"')])
                      for name, code in PYTHON.items()]
            for name, command in cases:
                control = run_bounded(command, work / ("native-" + name), timeout_ms)
                assert control["exit_code"] == 0 and control["stdout"].strip() == "ok", control
                for index in range(args.repeats):
                    path = work / f"{index}-{name}"
                    report = path / "report.json"
                    run = [str(args.runner_bin.resolve()), "run", "--drrun", str(drrun), "--client",
                           str(args.client.resolve()), "--report", str(report), "--timeout-ms", str(timeout_ms),
                           "--", *command]
                    row = run_bounded(run, path, timeout_ms)
                    row["case"] = f"{index}-{name}"
                    envelope = json.loads(report.read_text()) if report.exists() else {}
                    row["report"] = envelope
                    measurement, execution = envelope.get("measurement", {}), envelope.get("execution", {})
                    row["passed"] = (row["exit_code"] == 0 and row["stdout"].strip() == "ok"
                                     and not row["harness_timeout"] and row["harness_cleanup_complete"]
                                     and execution.get("cleanup_complete") is True
                                     and execution.get("records_cleanup_complete") is True
                                     and execution.get("exit_code") == 0
                                     and measurement.get("valid") is True and measurement.get("complete") is True)
                    rows.append(row)
    finally:
        cleaned = cleanup_orphans()
        libc.prctl(36, previous, 0, 0, 0)
    summary = {"passed": sum(row["passed"] for row in rows), "cases": len(rows),
               "failures": [row["case"] for row in rows if not row["passed"]],
               "cleanup_complete": cleaned, "engine_debug": args.engine_debug, "rows": rows}
    (args.output_dir / "results.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({key: value for key, value in summary.items() if key != "rows"}))
    return 0 if cleaned and rows and all(row["passed"] for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
