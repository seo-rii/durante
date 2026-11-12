#!/usr/bin/env python3
"""Run fixed native fixtures against an externally owned hardware counter.

Exit 0: the bounded fixture matrix qualified on this host; 77: unavailable;
1: a real observation or infrastructure check failed. This does not qualify a
trusted Durante backend, a fair time metric, or arbitrary hostile workloads.
"""
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
import time

MODES = ("control", "disable", "descriptor", "fork", "thread", "exec")
SIZES = (200_000, 2_000_000)
EXPECTED_DELTA = 4 * (SIZES[1] - SIZES[0])
U64_MAX = (1 << 64) - 1
SOURCE = Path(__file__).with_suffix(".c")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def uint(value):
    return type(value) is int and 0 <= value <= U64_MAX


def inspect_observation(row):
    """No count can qualify without actual completion and full counter coverage."""
    if row.get("state") != "observed" or row.get("stage") != "complete":
        return "probe did not observe a completed fixture"
    if row.get("cleanup_complete") is not True or type(row.get("exit_code")) is not int or row["exit_code"] != 0:
        return "fixture or descendant cleanup did not complete normally"
    if type(row.get("probe_exit_code")) is not int or row["probe_exit_code"] != 0:
        return "controller did not complete normally"
    if not uint(row.get("root_pid")) or row["root_pid"] == 0:
        return "missing owned process identity"
    if not uint(row.get("reaped")) or row["reaped"] == 0:
        return "fixture was not reaped"
    for field in ("instructions", "time_enabled_ns", "time_running_ns"):
        if not uint(row.get(field)) or row[field] == 0:
            return "missing or invalid hardware count/coverage"
    if row["time_enabled_ns"] != row["time_running_ns"]:
        return "partial PMU scheduling is not an admissible unscaled observation"
    return None


def inspect_matrix(rows, repeats):
    expected = {(mode, size, repeat) for mode in MODES for size in SIZES
                for repeat in range(repeats)}
    indexed = {}
    for row in rows:
        key = (row.get("mode"), row.get("iterations"), row.get("repeat"))
        if key not in expected or key in indexed:
            return "duplicate or unexpected fixture observation"
        error = inspect_observation(row)
        if error:
            return error
        indexed[key] = row
    if set(indexed) != expected:
        return "fixture matrix is incomplete"
    # Fixed ordinary integer instructions dominate the dynamic loader and
    # syscall wrappers. A 1% tolerance is a feasibility threshold, not proof of
    # exact retired counts or a fairness guarantee for arbitrary instruction sets.
    tolerance = EXPECTED_DELTA // 100
    for mode in MODES:
        for repeat in range(repeats):
            delta = (indexed[mode, SIZES[1], repeat]["instructions"] -
                     indexed[mode, SIZES[0], repeat]["instructions"])
            if abs(delta - EXPECTED_DELTA) > tolerance:
                return f"work increment missing or distorted in {mode} fixture"
    return None


def host_metadata():
    result = {"system": platform.system(), "machine": platform.machine(),
              "kernel": platform.release()}
    try:
        result["perf_event_paranoid"] = int(Path("/proc/sys/kernel/perf_event_paranoid").read_text())
        cpu = Path("/proc/cpuinfo").read_text().split("\n\n", 1)[0]
        wanted = {"vendor_id", "cpu family", "model", "model name", "stepping", "microcode"}
        result["cpu"] = {key.strip(): value.strip() for line in cpu.splitlines()
                         if ":" in line for key, value in [line.split(":", 1)]
                         if key.strip() in wanted}
    except (OSError, ValueError):
        result["platform_metadata_incomplete"] = True
    return result


def reap_adopted():
    """Only use in this dedicated harness, after its direct subprocess is waited."""
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return True
        if pid:
            continue
        try:
            children = Path(f"/proc/self/task/{os.getpid()}/children").read_text().split()
            for raw in children:
                try:
                    os.kill(int(raw), signal.SIGKILL)
                except ProcessLookupError:
                    pass
        except OSError:
            return False
        time.sleep(0.005)
    return False


def run_probe(binary, mode, iterations, native=False):
    # Only the supplied, source-hashed fixed fixtures can execute. The C helper
    # owns and reaps its root and any subreaper-adopted descendant; it has a 3 s
    # execution deadline and a 2 s cleanup deadline.
    command = [str(binary), *(["--native"] if native else []), mode, str(iterations)]
    process = subprocess.Popen(command,
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, start_new_session=True)
    try:
        output, diagnostics = process.communicate(timeout=8)
    except subprocess.TimeoutExpired:
        # Bounded fallback for a failed helper. This known fixture family never
        # daemonizes; a SIGTERM lets the helper complete its owned cleanup.
        process.terminate()
        try:
            output, diagnostics = process.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            output, diagnostics = process.communicate(timeout=3)
        return {"state": "failed", "stage": "outer_deadline", "cleanup_complete": reap_adopted()}
    adopted_cleanup = reap_adopted()
    if len(output) > 4096 or len(diagnostics) > 4096:
        return {"state": "failed", "stage": "oversized_probe_output", "cleanup_complete": False}
    try:
        row = json.loads(output)
        if type(row) is not dict:
            raise ValueError("non-object")
    except (ValueError, UnicodeError):
        return {"state": "failed", "stage": "invalid_probe_output", "cleanup_complete": False}
    row["probe_exit_code"] = process.returncode
    if diagnostics:
        row["unexpected_diagnostics"] = True
        row["state"] = "failed"
    expected = {"observed": 0, "native_control": 0, "unavailable": 77, "failed": 1}.get(row.get("state"))
    if process.returncode != expected or row.get("cleanup_complete") is not True or not adopted_cleanup:
        row["state"] = "failed"
        row["cleanup_complete"] = row.get("cleanup_complete") is True and adopted_cleanup
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True,
                        help="new directory for the helper binary and result.json")
    parser.add_argument("--repeats", type=int, choices=range(1, 6), default=3)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, mode=0o700, exist_ok=False)
    binary = args.output_dir.resolve() / "external-perf"
    result = {"schema": 1, "authority": "diagnostic_only", "integrity_verified": False,
              "experiment": "external_perf_feasibility", "qualification": "failed",
              "metric": "kernel_perf_user_instructions", "host": host_metadata(),
              "production_backend_qualified": False,
              "source_sha256_before": digest(SOURCE), "native_controls": [], "rows": [],
              "event": {"type": "PERF_TYPE_HARDWARE", "config": "PERF_COUNT_HW_INSTRUCTIONS",
                        "exclude_kernel": True, "exclude_hv": True, "inherit": True,
                        "pinned": True, "enable_on_exec": True, "scaled": False},
              "expected_work_delta": EXPECTED_DELTA, "delta_tolerance": EXPECTED_DELTA // 100}
    started = time.monotonic()
    try:
        if platform.system() != "Linux" or platform.machine() != "x86_64":
            result["qualification"] = "unavailable"
            result["reason"] = "experiment requires Linux x86-64"
        else:
            # If a helper is killed unexpectedly, adopt and reap its known
            # fixture family instead of relying on the host's PID 1 to do so.
            libc = ctypes.CDLL(None, use_errno=True)
            if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
                raise OSError(ctypes.get_errno(), "subreaper setup failed")
            compile_result = subprocess.run(
                ["cc", "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-pthread",
                 str(SOURCE), "-o", str(binary)], stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, timeout=60, check=False)
            if compile_result.returncode:
                raise ValueError("probe compilation failed")
            result["binary_sha256_before"] = digest(binary)
            for mode in MODES:
                for size in SIZES:
                    row = run_probe(binary, mode, size, native=True)
                    row.update(mode=mode, iterations=size)
                    result["native_controls"].append(row)
                    if row["state"] != "native_control" or row.get("exit_code") != 0:
                        result["reason"] = "native fixture control failed"
                        raise StopIteration
            for mode in MODES:
                for repeat in range(args.repeats):
                    for size in SIZES:
                        row = run_probe(binary, mode, size)
                        row.update(mode=mode, iterations=size, repeat=repeat)
                        result["rows"].append(row)
                        if row["state"] != "observed":
                            result["qualification"] = row["state"]
                            result["reason"] = "hardware observation unavailable" if row["state"] == "unavailable" else "fixture probe failed"
                            raise StopIteration
            error = inspect_matrix(result["rows"], args.repeats)
            result["qualification"] = "failed" if error else "supported_for_fixtures"
            if error:
                result["reason"] = error
    except StopIteration:
        pass
    except (OSError, ValueError, subprocess.TimeoutExpired):
        result["qualification"] = "failed"
        result["reason"] = "experiment infrastructure failed"
    finally:
        result["source_sha256_after"] = digest(SOURCE)
        if binary.is_file():
            result["binary_sha256_after"] = digest(binary)
        if result["source_sha256_before"] != result["source_sha256_after"] or (
                "binary_sha256_before" in result and
                result["binary_sha256_before"] != result.get("binary_sha256_after")):
            result["qualification"] = "failed"
            result["reason"] = "experiment inputs changed during observation"
        result["wall_seconds"] = round(time.monotonic() - started, 3)
        path = args.output_dir / "result.json"
        with path.open("x") as stream:
            json.dump(result, stream, indent=2)
            stream.write("\n")
        os.chmod(path, 0o600)
    print(json.dumps({"qualification": result["qualification"],
                      "native_controls": len(result["native_controls"]),
                      "hardware_attempts": len(result["rows"]),
                      "accepted_observations": sum(row["state"] == "observed" for row in result["rows"]),
                      "result": str(path)}))
    return 0 if result["qualification"] == "supported_for_fixtures" else (
        77 if result["qualification"] == "unavailable" else 1)


if __name__ == "__main__":
    sys.exit(main())
