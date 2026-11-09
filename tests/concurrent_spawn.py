#!/usr/bin/env python3
"""Bounded concurrent-spawn regression with exact static leaf instruction counts."""
import argparse
import ctypes
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import tempfile
import time

from fixtures import LOOP

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("reference_collector", ROOT / "collect.py")
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)


def bounded_number(low, high):
    def parse(value):
        number = int(value)
        if not low <= number <= high:
            raise argparse.ArgumentTypeError(f"value must be in {low}..{high}")
        return number
    return parse


def own_subreaper():
    """Protect the test host even if the supervisor under test itself fails."""
    libc = ctypes.CDLL(None, use_errno=True)
    old = ctypes.c_int()
    if libc.prctl(37, ctypes.byref(old), 0, 0, 0) or libc.prctl(36, 1, 0, 0, 0):
        raise OSError(ctypes.get_errno(), "cannot own orphaned test descendants")
    return libc, old.value


def cleanup_orphans():
    """The single-threaded harness owns no other asynchronous children."""
    until = time.monotonic() + 3
    while True:
        try:
            while os.waitpid(-1, os.WNOHANG)[0]:
                pass
        except ChildProcessError:
            return True
        if time.monotonic() >= until:
            return False
        # Direct unreaped children cannot have their PIDs reused before waitpid.
        with open(f"/proc/self/task/{os.getpid()}/children", "rb") as stream:
            data = stream.read(65536)
        if len(data) == 65536:
            data = data[:data.rfind(b" ") + 1]
        for token in data.split():
            try:
                os.kill(int(token), signal.SIGKILL)
            except ProcessLookupError:
                pass
        time.sleep(0.005)


def compile_fixture(work):
    executable = work / "concurrent-spawn"
    subprocess.run(["gcc", "-O2", "-Wall", "-Wextra", "-Werror", "-pthread",
                    str(ROOT / "tests/concurrent_spawn.c"), "-o", str(executable)],
                   check=True, umask=0o022)
    leaves = {}
    for iterations in (100000, 150000):
        for delayed in (False, True):
            name = f"leaf-{iterations}" + ("-delayed" if delayed else "")
            source = LOOP.replace("$100000", f"${iterations}")
            if delayed:
                source = source.replace("_start:\n", """_start:
    mov $35, %eax
    lea delay(%rip), %rdi
    xor %esi, %esi
    syscall
""") + "\n.section .rodata\n.align 8\ndelay: .quad 0, 500000000\n"
            asm = work / (name + ".S")
            asm.write_text(source)
            program = work / name
            subprocess.run(["gcc", "-static", "-nostdlib", "-no-pie", str(asm),
                            "-o", str(program)], check=True, umask=0o022)
            leaves[(iterations, delayed)] = program
    return executable, leaves


def read_excerpt(path):
    with path.open("rb") as stream:
        return stream.read(16384).decode(errors="replace")


def run_case(args, work, executable, leaf, mode, index, iterations):
    label = f"{index:03d}-{mode}"
    directory = work / label
    directory.mkdir()
    records = directory / "records"
    report = directory / "report.json"
    stdout, stderr = directory / "stdout.txt", directory / "stderr.txt"
    command = [str(args.runner_bin), "run", "--drrun", str(args.drrun),
               "--client", str(args.client), "--report", str(report),
               "--records-dir", str(records), "--timeout-ms", str(args.timeout_ms),
               "--", str(executable), mode, str(leaf), str(args.threads), str(args.rounds)]
    started = time.monotonic()
    harness_timeout = False
    with stdout.open("wb") as out, stderr.open("wb") as err:
        process = subprocess.Popen(command, stdout=out, stderr=err)
        try:
            code = process.wait(timeout=args.timeout_ms / 1000 + 5)
        except subprocess.TimeoutExpired:
            harness_timeout = True
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
    row = {"case": label, "mode": mode, "exit_code": code,
           "elapsed_s": round(time.monotonic() - started, 4),
           "leaf_iterations": iterations, "harness_timeout": harness_timeout,
           "harness_cleanup_complete": cleaned, "stdout": read_excerpt(stdout),
           "stderr": read_excerpt(stderr), "infrastructure_ok": cleaned and not harness_timeout,
           "passed": False, "errors": []}
    errors = row["errors"]
    if not cleaned or harness_timeout:
        errors.append("test supervisor exceeded its outer deadline or cleanup failed")
    if not report.is_file():
        row["infrastructure_ok"] = False
        errors.append("supervisor did not write a report")
        return row
    envelope = json.loads(report.read_text())
    execution, measurement = envelope["execution"], envelope["measurement"]
    row["execution"], row["measurement"] = execution, measurement
    if not execution["cleanup_complete"] or not execution["records_cleanup_complete"]:
        row["infrastructure_ok"] = False
        errors.append("supervisor cleanup failed")
    if not measurement.get("valid"):
        if measurement.get("complete") is not False or any(
                key in measurement for key in ("instructions", "rep_bytes", "rep_entries",
                                               "unsupported_rep_entries", "signals_observed", "processes", "segments")):
            raise AssertionError("invalid measurement exposed partial totals")
        errors.append("measurement unavailable")
        return row
    reference = collector.collect(records, execution["root_pid"])
    if measurement != reference:
        errors.append("Rust aggregate differs from the Python reference")
    if code != 0 or execution["exit_code"] != 0 or execution["state"] != "complete":
        errors.append("fixture or supervisor did not complete normally")
    if row["stdout"].strip() != "concurrent-spawn-ok":
        errors.append("fixture output mismatch")
    children = args.threads * args.rounds
    expected_images = 1 + children * (1 if mode.startswith("failed-exec") else 2)
    if measurement["process_count"] != 1 + children or measurement["image_count"] != expected_images:
        errors.append("missing, duplicated, or unexpected process/image count")
    processes = measurement["processes"]
    root = next(p for p in processes if p["pid"] == execution["root_pid"])
    parent_segment = next(s for s in measurement["segments"] if s["pid"] == root["pid"])
    if parent_segment["threads"] != args.threads + 1:
        errors.append("parent thread accounting mismatch")
    expected_leaf = 3 * iterations + 5 + (4 if mode == "orphan" else 0)
    final_leaves = [s for s in measurement["segments"]
                    if s["pid"] != root["pid"] and s["exit_code"] is not None]
    if len(final_leaves) != children:
        errors.append("missing child terminal images")
    if mode.startswith("failed-exec"):
        if any(p["exit_code"] != 127 or p["images"] != 1
               for p in processes if p["pid"] != root["pid"]):
            errors.append("failed-exec child status or image count mismatch")
    elif any(s["instructions"] != expected_leaf or s["exit_code"] != 0 for s in final_leaves):
        errors.append("static leaf instruction count or exit status mismatch")
    if mode == "orphan" and execution["supervisor_reaped_processes"] <= 1:
        errors.append("orphan scenario did not outlive its parent")
    for counter in ("instructions", "rep_bytes", "rep_entries", "unsupported_rep_entries", "signals_observed"):
        if measurement[counter] != sum(p[counter] for p in processes) or measurement[counter] != sum(
                s[counter] for s in measurement["segments"]):
            errors.append("process/image totals disagree")
    row["passed"] = not errors
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk", type=Path, required=True)
    parser.add_argument("--client", type=Path, required=True)
    parser.add_argument("--runner-bin", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--repeats", type=bounded_number(1, 200), default=20)
    parser.add_argument("--threads", type=bounded_number(2, 16), default=3)
    parser.add_argument("--rounds", type=bounded_number(1, 64), default=8)
    parser.add_argument("--timeout-ms", type=bounded_number(1000, 60000), default=5000)
    parser.add_argument("--modes", nargs="+", choices=("success", "failed-exec", "failed-exec-handler", "empty-env", "orphan"),
                        default=["success", "failed-exec", "failed-exec-handler", "empty-env", "orphan"])
    parser.add_argument("--diagnostic", action="store_true", help="record baseline failures without claiming qualification")
    parser.add_argument("--engine-debug", action="store_true", help="exercise the SDK's debug core and assertions")
    args = parser.parse_args()
    if args.threads * args.rounds > 240:
        parser.error("threads times rounds must not exceed 240")
    args.sdk, args.client, args.runner_bin = [p.resolve() for p in (args.sdk, args.client, args.runner_bin)]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    source_paths = [ROOT / "tests/concurrent_spawn.c", Path(__file__).resolve(),
                    ROOT / "tests/fixtures.py", ROOT / "collect.py"]
    source_hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                     for p in source_paths}
    work = Path(tempfile.mkdtemp(prefix="cases-", dir=args.output_dir.resolve()))
    args.drrun = args.sdk / "bin64/drrun"
    if args.engine_debug:
        # The wrapper execs the trusted frontend, preserving the measured root
        # PID without extending the production CLI's engine-option surface.
        wrapper = work / "drrun-debug"
        wrapper.write_text(f'#!/bin/sh\nexec {shlex.quote(str(args.drrun))} -debug "$@"\n')
        wrapper.chmod(0o755)
        args.drrun = wrapper
    core = args.sdk / "lib64" / ("debug" if args.engine_debug else "release") / "libdynamorio.so"
    binary_hashes = {}
    for label, path in (("core", core), ("client", args.client), ("runner", args.runner_bin)):
        with path.open("rb") as stream:
            binary_hashes[label] = hashlib.file_digest(stream, "sha256").hexdigest()
    executable, leaves = compile_fixture(work)
    libc, old_subreaper = own_subreaper()
    rows = []
    try:
        for index in range(args.repeats):
            iterations = 100000 if index % 2 == 0 else 150000
            # Reverse mode order on alternating repetitions to avoid a fixed sequence.
            for mode in args.modes if index % 2 == 0 else list(reversed(args.modes)):
                rows.append(run_case(args, work, executable, leaves[(iterations, mode == "orphan")],
                                     mode, index, iterations))
    finally:
        cleaned = cleanup_orphans()
        libc.prctl(36, old_subreaper, 0, 0, 0)
    failures = [row["case"] for row in rows if not row["passed"]]
    sources_unchanged = all(hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == digest
                            for path, digest in source_hashes.items())
    infrastructure_ok = cleaned and sources_unchanged and all(row["infrastructure_ok"] for row in rows)
    exit_code = 0 if infrastructure_ok and (not failures or args.diagnostic) else 1
    summary = {"exit_code": exit_code, "diagnostic_only": args.diagnostic,
               "cases": len(rows), "passed": len(rows) - len(failures), "failures": failures,
               "threads": args.threads, "rounds": args.rounds,
               "harness_cleanup_complete": cleaned,
               "source_unchanged": sources_unchanged, "infrastructure_ok": infrastructure_ok,
               "inputs": source_hashes, "engine_debug": args.engine_debug,
               "binary_sha256": binary_hashes,
               "rows": rows}
    (args.output_dir / "results.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({key: value for key, value in summary.items() if key != "rows"}))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
