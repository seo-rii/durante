#!/usr/bin/env python3
"""Bounded native/engine/client qualification of signal vector and FP state."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import time

from concurrent_spawn import bounded_number, cleanup_orphans, collector, own_subreaper
from runtime_compat import inspect_accounting

ROOT = Path(__file__).resolve().parents[1]
ISAS = ("sse2", "avx2", "avx512")
CASES = ("fault", "async")
MAX_CAPTURE = 65536
FAILURE_REASONS = {
    60: "handler was outside the alternate stack", 61: "unexpected nested delivery",
    62: "unexpected outer delivery", 63: "fault address or saved RIP changed",
    70: "missing floating-point frame", 71: "saved x87 control word changed",
    72: "saved x87 status or stack tag changed", 73: "saved MXCSR changed",
    74: "saved ST(0) changed", 75: "saved XMM15 changed", 76: "saved YMM15 upper half changed",
    77: "saved ZMM31 changed", 78: "saved K7 changed", 79: "required ISA unavailable inside fixture",
    80: "missing XSAVE frame magic", 81: "invalid XSAVE frame size", 82: "invalid XSAVE tail magic",
    83: "missing XSAVE component or unsupported compacted frame", 84: "invalid XSAVE component bounds",
    85: "vector state changed on return", 86: "x87 control word changed on return",
    87: "x87 status changed on return", 88: "MXCSR changed on return", 89: "ST(0) changed on return",
    90: "K7 changed on return", 91: "rounded arithmetic result changed",
    92: "expected signal/frame/stack checks did not complete",
}


def expected_stdout(case, isa):
    return json.dumps({"case": case, "isa": isa, "outer": 1, "nested": 1,
                       "frames": 2, "altstack": 2, "restored": True}, separators=(",", ":")) + "\n"


def qualification_errors(row, case, isa, measurement=None):
    errors = []
    if row["exit_code"] != 0 or row["timeout"]:
        errors.append("fixture did not finish normally before its deadline")
        if row["exit_code"] in FAILURE_REASONS:
            errors.append(FAILURE_REASONS[row["exit_code"]])
    if not row["cleanup_complete"]:
        errors.append("descendant cleanup incomplete")
    if row["stdout"] != expected_stdout(case, isa):
        errors.append("signal frame, arithmetic or restored-state oracle failed")
    if row.get("engine") != "debug" and row.get("stderr", "") != "":
        errors.append("unexpected native or release-runtime stderr")
    if row.get("capture_error"):
        errors.append(row["capture_error"])
    if measurement is not None:
        if measurement.get("valid") is not True or measurement.get("complete") is not True:
            errors.append("complete lifecycle accounting unavailable")
        if measurement.get("integrity_verified") is not False or measurement.get("measurement_contract") != collector.measurement_contract():
            errors.append("missing or promoted diagnostic contract")
        if measurement.get("quality") != "signals_observed" or measurement.get("signals_observed") != 2:
            errors.append("both handled signals must remain visible")
        if measurement.get("process_count") != 1 or measurement.get("image_count") != 1:
            errors.append("unexpected process or image count")
    return errors


def bounded_read(path, maximum):
    with path.open("rb") as stream:
        data = stream.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError("captured output exceeds inspection bound")
    return data


def run(command, directory, timeout_ms):
    directory.mkdir(mode=0o700)
    limited = [shutil.which("prlimit"), "--as=2147483648", "--cpu=15",
               "--fsize=1048576", "--core=0", "--", *command]
    started = time.monotonic()
    timed_out = False
    with (directory / "stdout.txt").open("wb") as out, (directory / "stderr.txt").open("wb") as err:
        process = subprocess.Popen(limited, cwd=directory, stdin=subprocess.DEVNULL,
                                   stdout=out, stderr=err, start_new_session=True)
        try:
            code = process.wait(timeout=timeout_ms / 1000)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(process.pid, signal.SIGTERM)
            try:
                code = process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                code = process.wait(timeout=3)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=3)
            cleaned = cleanup_orphans()
    row = {"root_pid": process.pid, "exit_code": code, "timeout": timed_out,
           "cleanup_complete": cleaned, "elapsed_s": round(time.monotonic() - started, 4),
           "command": command, "stdout": "", "stderr": ""}
    try:
        row["stdout"] = bounded_read(directory / "stdout.txt", MAX_CAPTURE).decode("utf-8")
        row["stderr"] = bounded_read(directory / "stderr.txt", MAX_CAPTURE).decode("utf-8", errors="replace")
    except (OSError, ValueError, UnicodeError) as error:
        row["capture_error"] = str(error)
    return row


def inputs(args):
    files = {name: ROOT / name for name in (
        "tests/signal_state.c", "tests/signal_state.py", "tests/test_signal_state.py",
        "tests/simd_probe.c", "tests/runtime_compat.py", "tests/concurrent_spawn.py", "collect.py")}
    files.update(client=args.client, collector=args.runner_bin, drrun=args.sdk / "bin64/drrun",
                 release_core=args.sdk / "lib64/release/libdynamorio.so",
                 debug_core=args.sdk / "lib64/debug/libdynamorio.so",
                 runtime_manifest=args.sdk / ".durante-runtime.json")
    return {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in files.items()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("sdk", "client", "runner-bin", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--require-isa", choices=ISAS, action="append", default=[])
    parser.add_argument("--repeats", type=bounded_number(1, 5), default=2)
    parser.add_argument("--timeout-ms", type=bounded_number(1000, 20000), default=15000)
    args = parser.parse_args()
    if platform.system() != "Linux" or platform.machine() not in ("x86_64", "AMD64"):
        parser.error("signal-state fixtures require Linux x86-64")
    if not shutil.which("prlimit"):
        parser.error("Linux prlimit is required")
    args.sdk, args.client, args.runner_bin, args.output_dir = [p.resolve() for p in (
        args.sdk, args.client, args.runner_bin, args.output_dir)]
    args.output_dir.mkdir(parents=True, mode=0o700, exist_ok=False)
    initial = inputs(args)
    rows, skipped, failures = [], [], []
    probe, binary_hashes = {}, {}
    fatal = None
    libc, previous = own_subreaper()
    try:
        executable, probe_binary = args.output_dir / "signal-state", args.output_dir / "isa-probe"
        for source, output, flags in (
                ("tests/signal_state.c", executable, ["-mgeneral-regs-only"]),
                ("tests/simd_probe.c", probe_binary, ["-march=x86-64", "-mno-avx", "-mno-avx2", "-mno-avx512f"])):
            subprocess.run(["gcc", "-std=c17", "-O2", "-Wall", "-Wextra", "-Werror", *flags,
                            str(ROOT / source), "-o", str(output)], check=True, timeout=30)
            binary_hashes[output.name] = hashlib.sha256(output.read_bytes()).hexdigest()
        probe_row = run([str(probe_binary)], args.output_dir / "probe", 5000)
        if probe_row["exit_code"] or probe_row["timeout"] or not probe_row["cleanup_complete"] or probe_row.get("capture_error"):
            raise RuntimeError("native feature probe failed")
        probe = json.loads(probe_row["stdout"])
        for isa in ISAS:
            if probe.get(isa) is not True:
                skipped.append({"isa": isa, "reason": "native CPU/OS extended state unavailable", "required": isa in args.require_isa})
                if isa in args.require_isa:
                    failures.append("required ISA unavailable: " + isa)
                continue
            for case in CASES:
                target = [str(executable), case, isa]
                name = f"{isa}-{case}-native"
                native = run(target, args.output_dir / name, args.timeout_ms)
                native.update(case=case, isa=isa, mode="native", engine="none", name=name)
                native["errors"] = qualification_errors(native, case, isa)
                native["passed"] = not native["errors"]
                rows.append(native)
                if not native["passed"]:
                    failures.append(name)
                    continue
                for engine in ("release", "debug"):
                    for mode in ("engine-only", "rep-off", "rep-on"):
                        for repeat in range(args.repeats):
                            name = f"{isa}-{case}-{engine}-{mode}-{repeat}"
                            directory = args.output_dir / name
                            records = args.output_dir / (name + "-records")
                            command = [str(args.sdk / "bin64/drrun"), *(["-debug"] if engine == "debug" else []),
                                       "-quiet", "-follow_children", "-vm_size", "32M", "-vmheap_size", "32M"]
                            if mode != "engine-only":
                                records.mkdir(mode=0o700)
                                command += ["-c", str(args.client), "-outdir", str(records)]
                                if mode == "rep-on":
                                    command.append("-rep_bytes")
                            row = run([*command, "--", *target], directory, args.timeout_ms)
                            row.update(case=case, isa=isa, mode=mode, engine=engine, name=name)
                            row["errors"] = qualification_errors(row, case, isa)
                            if mode != "engine-only":
                                collected = run([str(args.runner_bin), "collect", "--dir", str(records),
                                                 "--root-pid", str(row["root_pid"])], directory / "collector", 5000)
                                try:
                                    if collected["exit_code"] not in (0, 1) or collected["timeout"] or not collected["cleanup_complete"] or collected.get("capture_error"):
                                        raise ValueError("collector execution failed")
                                    measurement = json.loads(collected["stdout"])
                                    if collected["exit_code"] != (0 if measurement.get("valid") is True else 1):
                                        raise ValueError("collector exit status contradicts its report")
                                    row["measurement"] = measurement
                                    row["errors"] = qualification_errors(row, case, isa, measurement)
                                    accounting, _ = inspect_accounting(measurement, records, row["root_pid"], 1,
                                                                       mode == "rep-on", allow_handled_signals=True)
                                    row["errors"].extend(accounting)
                                except (OSError, ValueError, KeyError, TypeError) as error:
                                    row["errors"].append("accounting inspection failed: " + str(error))
                            row["passed"] = not row["errors"]
                            rows.append(row)
                            if not row["passed"]:
                                failures.append(name)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        fatal = str(error)
    finally:
        cleaned = cleanup_orphans()
        libc.prctl(36, previous, 0, 0, 0)
    try:
        unchanged = inputs(args) == initial
    except OSError:
        unchanged = False
    code = int(bool(fatal or failures or not cleaned or not unchanged or not rows))
    summary = {"schema": 1, "authority": "diagnostic_only", "exit_code": code,
               "cases": len(rows), "passed": sum(row["passed"] for row in rows),
               "failures": failures, "skipped": skipped, "fatal_error": fatal,
               "probe": probe, "cleanup_complete": cleaned, "inputs_unchanged": unchanged,
               "inputs": initial, "fixture_sha256": binary_hashes, "rows": rows,
               "limitations": ["fixed Linux x86-64 signal-frame fixtures only",
                               "unsupported ISA is skipped, never passed",
                               "asynchronous instruction totals depend on delivery timing",
                               "no claim of retired instructions, tamper resistance or CPU-time conversion"]}
    (args.output_dir / "results.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({key: value for key, value in summary.items() if key not in ("rows", "inputs", "fixture_sha256")}))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
