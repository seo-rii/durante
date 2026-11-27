#!/usr/bin/env python3
"""Deliver signals whose frame cannot be written to the stack.

The kernel then delivers SIGSEGV instead, and when the frame for SIGSEGV itself
cannot be written it makes SIGSEGV fatal (force_sigsegv()). tests/signal_stack.c
reaches this with a stack overflow that a SIGSEGV handler shares, and with a
SIGILL fault and a pending SIGUSR1 while the stack pointer is unwritable. Each
case runs natively, under the release and debug engines and under `durante
run`, with an 8 MiB stack. Every run must end as CASES says: the native run
checks the expectation, the others the runtime. A runtime that loops instead
reaches the per-run deadline.

  python3 tests/signal_stack.py --sdk SDK --client build/libdurante.so \\
      --runner-bin target/release/durante --output-dir NEW_DIR
"""
import argparse
import json
from pathlib import Path
import resource
import signal
import subprocess

ROOT = Path(__file__).resolve().parents[1]
# The termination of each case: an exit status, or a negative signal number.
CASES = {
    "overflow-handler": -signal.SIGSEGV,
    "overflow-altstack": 42,
    "ill": -signal.SIGSEGV,
    "ill-alt": 43,
    "usr1": -signal.SIGSEGV,
    "usr1-alt": 43,
    "usr1-segv": -signal.SIGSEGV,
}
STACK_BYTES = 8 << 20
RUNTIME_DIAGNOSTICS = ("Internal Error", "internal crash", "Please report")


def limits():
    resource.setrlimit(resource.RLIMIT_STACK, (STACK_BYTES, resource.getrlimit(resource.RLIMIT_STACK)[1]))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def run(command, timeout_s):
    try:
        proc = subprocess.run(command, capture_output=True, text=True, errors="replace",
                              timeout=timeout_s, preexec_fn=limits, start_new_session=True)
    except subprocess.TimeoutExpired:
        return None, ""
    return proc.returncode, proc.stderr


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for name in ("sdk", "client", "runner-bin", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--timeout-ms", type=int, default=20000)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    fixture = args.output_dir / "signal-stack"
    subprocess.run(["gcc", "-std=c17", "-O1", "-static", "-Wall", "-Wextra", "-Werror",
                    str(ROOT / "tests/signal_stack.c"), "-o", str(fixture)], check=True, timeout=60)
    drrun = str(args.sdk / "bin64/drrun")
    timeout_s = args.timeout_ms / 1000
    rows, failures = [], []
    for case, expected in CASES.items():
        target = [str(fixture), case]
        runs = {"native": target,
                "engine-release": [drrun, "-quiet", "--", *target],
                "engine-debug": [drrun, "-quiet", "-debug", "--", *target]}
        for name, command in runs.items():
            status, stderr = run(command, timeout_s)
            row = {"case": case, "run": name, "status": status, "expected": expected}
            if status != expected:
                row["error"] = "deadline reached" if status is None else f"ended with {status}"
            elif any(text in stderr for text in RUNTIME_DIAGNOSTICS):
                row["error"] = "runtime diagnostic on stderr"
            rows.append(row)
        report = args.output_dir / f"{case}.json"
        status, stderr = run([str(args.runner_bin), "run", "--drrun", drrun, "--client", str(args.client),
                              "--report", str(report), "--timeout-ms", str(args.timeout_ms), "--", *target],
                             timeout_s + 10)
        row = {"case": case, "run": "supervisor", "status": status, "expected": expected}
        try:
            execution = json.loads(report.read_text())["execution"]
        except (OSError, ValueError, KeyError):
            execution = None
        row["execution"] = execution and {k: execution.get(k) for k in ("state", "exit_code", "signal")}
        if expected < 0:
            wanted = {"state": "incomplete", "exit_code": None, "signal": -expected}
        else:
            wanted = {"state": "complete", "exit_code": expected, "signal": None}
        if row["execution"] != wanted:
            row["error"] = f"expected {wanted}"
        elif any(text in stderr for text in RUNTIME_DIAGNOSTICS):
            row["error"] = "runtime diagnostic on stderr"
        rows.append(row)
    failures = [row for row in rows if "error" in row]
    summary = {"exit_code": 1 if failures else 0, "cases": len(rows), "passed": len(rows) - len(failures),
               "failures": [f"{row['case']}-{row['run']}: {row['error']}" for row in failures]}
    (args.output_dir / "results.json").write_text(json.dumps({**summary, "rows": rows}, indent=1) + "\n")
    print(json.dumps(summary))
    return summary["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
