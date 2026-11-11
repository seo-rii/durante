#!/usr/bin/env python3
"""Exact instruction counts and native-equivalent SIMD lanes; no time weights."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import platform
import re
import struct
import subprocess
import tempfile

from clone_clear_sighand import run_bounded
from concurrent_spawn import cleanup_orphans, own_subreaper

ROOT = Path(__file__).resolve().parents[1]
LEVELS = ("scalar", "sse2", "avx2", "avx512")
MAX_OUTPUT_BYTES = 386
# All instructions execute once. Common setup/check/write/exit = 13.
# scalar: 48 integer + 24 scalar FP + 2 live-register jumps.
# SSE2: 12 packed integer + 12 packed FP + 2 jumps.
# AVX2: 6 integer + 6 FP + 2 jumps + VZEROUPPER.
# AVX512: 8 unmasked integer/FP + 3 jumps + 2 mask setup +
#         5 masked integer + 5 masked FP + VZEROUPPER.
EXPECTED_INSTRUCTIONS = {"scalar": 87, "sse2": 39, "avx2": 28, "avx512": 37}
UNAVAILABLE = {
    "sse2": "CPUID.1 EDX.SSE2 is absent",
    "avx2": "requires CPUID AVX/XSAVE/OSXSAVE/AVX2 and XCR0 SSE+YMM state",
    "avx512": "requires AVX512F and XCR0 SSE+YMM+opmask+ZMM state (0xe6)",
}


def expected_lanes(level):
    integers = [101 + 2 * lane for lane in range(16)]
    floating = [1.5 * (lane + 1) for lane in range(8)]
    lanes = {"integer": integers, "floating": floating,
             "flags": [0, 1] if level == "scalar" else [1, 0]}
    if level == "avx512":
        lanes.update(integer_merge=[v if i % 2 == 0 else 0xffffffff for i, v in enumerate(integers)],
                     integer_zero=[v if i % 2 == 0 else 0 for i, v in enumerate(integers)],
                     floating_merge=[v if i % 2 == 0 else -1.0 for i, v in enumerate(floating)],
                     floating_zero=[v if i % 2 == 0 else 0.0 for i, v in enumerate(floating)])
    return lanes


def expected_output(level):
    lanes = expected_lanes(level)
    data = struct.pack("<16I8d", *lanes["integer"], *lanes["floating"])
    if level == "avx512":
        for key, fmt in (("integer_merge", "<16I"), ("integer_zero", "<16I"),
                         ("floating_merge", "<8d"), ("floating_zero", "<8d")):
            data += struct.pack(fmt, *lanes[key])
    # Scalar integer ADD changes ZF; packed/vector arithmetic preserves it.
    return data + bytes(lanes["flags"])


def decode_output(data, level):
    flags = 384 if level == "avx512" else 128
    if len(data) != flags + 2:
        return None
    def floating(offset):
        return [v if math.isfinite(v) else v.hex() for v in struct.unpack_from("<8d", data, offset)]
    lanes = {"integer": list(struct.unpack_from("<16I", data, 0)),
             "floating": floating(64), "flags": list(data[flags:])}
    if level == "avx512":
        lanes.update(integer_merge=list(struct.unpack_from("<16I", data, 128)),
                     integer_zero=list(struct.unpack_from("<16I", data, 192)),
                     floating_merge=floating(256), floating_zero=floating(320))
    return lanes


def run_capture(command, directory, timeout_ms=5000):
    row = run_bounded(command, directory, timeout_ms)
    path = directory / "stdout.txt"
    with path.open("rb") as stream:
        raw = stream.read(MAX_OUTPUT_BYTES + 1)
    row.pop("stdout", None)  # Arbitrary lane bytes are not UTF-8 text.
    row.pop("guest", None)
    row["stdout_hex"] = raw.hex()
    row["stdout_bytes"] = path.stat().st_size
    return row, raw


def input_hashes(args):
    paths = {name: ROOT / name for name in
             ("tests/simd.py", "tests/simd_fixture.S", "tests/simd_probe.c")}
    paths.update(client=args.client.resolve(), runner=args.runner_bin.resolve(),
                 drrun=args.sdk.resolve() / "bin64/drrun",
                 release_core=args.sdk.resolve() / "lib64/release/libdynamorio.so")
    hashes = {}
    for label, path in paths.items():
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1 << 20):
                digest.update(chunk)
        hashes[label] = digest.hexdigest()
    return hashes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk", type=Path, required=True)
    parser.add_argument("--client", type=Path, required=True)
    parser.add_argument("--runner-bin", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--require-isa", action="append", choices=LEVELS, default=[])
    parser.add_argument("--repeats", type=int, default=2)
    args = parser.parse_args()
    if platform.system() != "Linux" or platform.machine() not in ("x86_64", "AMD64"):
        parser.error("SIMD fixtures require Linux x86-64")
    if not 1 <= args.repeats <= 10:
        parser.error("repeats must be in 1..10")
    args.output_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    rows, controls, skipped, failures, assembled = [], [], [], [], []
    initial_hashes = input_hashes(args)
    libc, previous = own_subreaper()
    probe = {}
    try:
        with tempfile.TemporaryDirectory(prefix="simd-", dir=args.output_dir) as name:
            work = Path(name).resolve()
            probe_binary = work / "simd-probe"
            subprocess.run(["gcc", "-std=c17", "-O2", "-Wall", "-Wextra", "-Werror",
                            "-march=x86-64", "-mno-avx", "-mno-avx2", "-mno-avx512f",
                            str(ROOT / "tests/simd_probe.c"), "-o", str(probe_binary)], check=True)
            result = subprocess.run([str(probe_binary)], capture_output=True, text=True,
                                    timeout=5, check=True)
            probe = json.loads(result.stdout)
            drrun = args.sdk.resolve() / "bin64/drrun"
            no_rep = work / "drrun-no-rep"
            no_rep.write_text("#!/usr/bin/python3\nimport os,sys\n"
                              "args=sys.argv[1:]\nend=args.index('--')\n"
                              "args[:end]=[a for a in args[:end] if a!='-rep_bytes']\n"
                              f"path={str(drrun)!r}\nos.execv(path,[path,*args])\n")
            no_rep.chmod(0o700)
            for index, level in enumerate(LEVELS):
                programs = {}
                for target_exit in (0, 7):
                    executable = work / f"{level}-exit-{target_exit}"
                    subprocess.run(["gcc", "-static", "-nostdlib", "-no-pie",
                                    f"-DSIMD_LEVEL={index}", f"-DEXIT_CODE={target_exit}",
                                    str(ROOT / "tests/simd_fixture.S"), "-o", str(executable)],
                                   check=True)
                    disassembly = subprocess.run(["objdump", "-d", "--no-show-raw-insn", str(executable)],
                                                 capture_output=True, text=True, check=True, timeout=5)
                    static_count = sum(bool(re.match(r"^\s*[0-9a-f]+:\s+\S+", line))
                                       for line in disassembly.stdout.splitlines())
                    if static_count != EXPECTED_INSTRUCTIONS[level]:
                        raise AssertionError((level, "fixture static count changed", static_count))
                    assembled.append({"isa": level, "exit_code": target_exit,
                                      "instructions": static_count,
                                      "sha256": hashlib.sha256(executable.read_bytes()).hexdigest()})
                    programs[target_exit] = executable
                if probe.get(level) is not True:
                    skipped.append({"isa": level, "reason": UNAVAILABLE.get(level, "unavailable"),
                                    "required": level in args.require_isa})
                    if level in args.require_isa:
                        failures.append(f"required ISA unavailable: {level}")
                    continue
                expected, oracle = expected_output(level), expected_lanes(level)
                for target_exit in (0, 7):
                    executable = programs[target_exit]
                    control, output = run_capture([str(executable)], work / f"native-{level}-{target_exit}")
                    control.update(isa=level, expected_exit=target_exit,
                                   expected_instructions=EXPECTED_INSTRUCTIONS[level],
                                   executable_sha256=hashlib.sha256(executable.read_bytes()).hexdigest())
                    control["lanes"] = decode_output(output, level)
                    control["passed"] = (control["exit_code"] == target_exit and output == expected
                                         and control["lanes"] == oracle
                                         and not control["harness_timeout"]
                                         and control["harness_cleanup_complete"] and control["stderr"] == "")
                    controls.append(control)
                    if not control["passed"]:
                        failures.append(f"native control failed: {level}/{target_exit}")
                        continue
                    for rep in (False, True):
                        for repeat in range(args.repeats):
                            label = f"{level}-exit-{target_exit}-rep-{int(rep)}-{repeat}"
                            directory = work / label
                            report = directory / "report.json"
                            command = [str(args.runner_bin.resolve()), "run", "--drrun",
                                       str(drrun if rep else no_rep), "--client", str(args.client.resolve()),
                                       "--report", str(report), "--timeout-ms", "5000", "--", str(executable)]
                            row, output = run_capture(command, directory)
                            row.update(case=label, isa=level, target_exit=target_exit,
                                       rep_bytes_enabled=rep, expected_instructions=EXPECTED_INSTRUCTIONS[level])
                            row["lanes"] = decode_output(output, level)
                            envelope = json.loads(report.read_text()) if report.is_file() else {}
                            execution, measurement = envelope.get("execution", {}), envelope.get("measurement", {})
                            row["report"] = envelope
                            row["passed"] = (row["exit_code"] == 0 and output == expected
                                             and row["lanes"] == oracle
                                             and not row["harness_timeout"] and row["harness_cleanup_complete"]
                                             and row["stderr"] == "" and execution.get("exit_code") == target_exit
                                             and execution.get("state") == "complete"
                                             and execution.get("cleanup_complete") is True
                                             and execution.get("records_cleanup_complete") is True
                                             and measurement.get("valid") is True
                                             and measurement.get("complete") is True
                                             and measurement.get("quality") == "uninterrupted"
                                             and measurement.get("authority") == "diagnostic_only"
                                             and measurement.get("rep_bytes_enabled") is rep
                                             and measurement.get("instructions") == EXPECTED_INSTRUCTIONS[level]
                                             and measurement.get("process_count") == 1
                                             and measurement.get("image_count") == 1
                                             and all(measurement.get(key) == 0 for key in
                                                     ("rep_bytes", "rep_entries", "unsupported_rep_entries",
                                                      "signals_observed")))
                            rows.append(row)
                            if not row["passed"]:
                                failures.append(label)
    finally:
        cleaned = cleanup_orphans()
        libc.prctl(36, previous, 0, 0, 0)
    try:
        inputs_unchanged = input_hashes(args) == initial_hashes
    except OSError:
        inputs_unchanged = False
    if not inputs_unchanged:
        failures.append("source or installation inputs changed during qualification")
    summary = {"schema": 1, "authority": "diagnostic_only", "probe": probe,
               "passed": sum(row["passed"] for row in rows), "cases": len(rows),
               "native_controls_passed": sum(row["passed"] for row in controls),
               "assembled_fixtures": assembled,
               "native_controls": controls, "skipped": skipped, "failures": failures,
               "required_isa": args.require_isa, "cleanup_complete": cleaned,
               "input_sha256": initial_hashes, "inputs_unchanged": inputs_unchanged, "rows": rows}
    (args.output_dir / "results.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({key: value for key, value in summary.items()
                      if key not in ("rows", "native_controls")}))
    return 0 if cleaned and rows and not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
