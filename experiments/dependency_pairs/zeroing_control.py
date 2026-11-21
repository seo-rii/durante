#!/usr/bin/env python3
"""Compare Durante counters when only the DIV high-half zeroing idiom changes."""
import argparse
from fractions import Fraction
import json
import re
from pathlib import Path
import runpy
import subprocess
from tempfile import TemporaryDirectory

from fixtures import SOURCE, check_loop, loop_listing, sha
from study import HERE, ROOT, require

PRIOR = runpy.run_path(str(HERE.parent / "rust_operand_bytes/collect.py"))
READER = runpy.run_path(str(ROOT / "collect.py"))["collect"]
PLAN_BYTES = (HERE / "protocol.json").read_bytes()
PLAN = json.loads(PLAN_BYTES)
DIV_CASES = [case for case in PLAN["cases"] if case["pair"].startswith("div_")]
SUBSTITUTION = {"xor": "xor %edx, %edx", "mov": "mov $0, %edx"}


def source_for(variant):
    text = SOURCE.read_text()
    require(sha(text.encode()) == PLAN["build"]["kernel_source_sha256"] and
            text.count(SUBSTITUTION["xor"]) == 1, "expected one original DIV zeroing idiom")
    return text.replace(SUBSTITUTION["xor"], SUBSTITUTION[variant])


def check_disassembly(loop, case, variant):
    # The four substitutions expand the encoding but the loop branch target
    # and every other decoded hot-loop instruction must still match the ELF.
    expected = [ins.replace("xor %edx,%edx", "mov $0x0,%edx") if variant == "mov" else ins
                for ins in case["hot_loop"]]
    require(loop == expected, "unexpected DIV control disassembly")
    check_loop([ins.replace("mov $0x0,%edx", "xor %edx,%edx") for ins in loop], False, case["lanes"])


def verify(data):
    require(data["schema"] == 1 and data["protocol_sha256"] == sha(PLAN_BYTES) and data["tools"] == PLAN["tool_sha256"] and
            data["source_sha256"] == {v: sha(source_for(v).encode()) for v in SUBSTITUTION} and
            data["compiler"] == PLAN["build"]["compiler"] and data["linker"] == PLAN["build"]["linker"],
            "wrong control provenance")
    require(set(data["cases"]) == {c["workload_id"] for c in DIV_CASES}, "wrong DIV cases")
    for case in DIV_CASES:
        runs = data["cases"][case["workload_id"]]
        require(set(runs) == set(SUBSTITUTION) and runs["xor"]["binary_sha256"] == case["binary_sha256"] and
                runs["xor"]["binary_sha256"] != runs["mov"]["binary_sha256"], "wrong DIV binaries")
        for variant, row in runs.items():
            require(type(row["pid"]) is int and 0 < row["pid"] < 2**31 and
                    re.fullmatch(r"[0-9a-f]{64}", row["binary_sha256"]) and
                    isinstance(row["record_stream"], str) and len(row["record_stream"]) < 64 * 1024,
                    "invalid counter receipt")
            check_disassembly(row["hot_loop"], case, variant)
            require(row["stdout_hex"] == case["stdout_hex"] and row["exit_code"] == 0, "target failed")
            with TemporaryDirectory() as tmp:
                records = Path(tmp)
                (records / f"process-{row['pid']}.jsonl").write_text(row["record_stream"])
                counts = PRIOR["production"](records, row["pid"], "operand_bytes", READER)
            require(counts["instructions"] == case["expected_instructions"] and
                    counts["memory_operand_bytes"]["bytes"] == case["expected_operand_bytes"] and
                    counts["rep_bytes"] == 0 and counts["latency_bound"]["cycles_x16"] == row["cycles_x16"],
                    "wrong DIV counters")
    for pair in sorted({c["pair"] for c in DIV_CASES}):
        for variant in SUBSTITUTION:
            serial, parallel = (data["cases"][pair + "_" + kind][variant]["cycles_x16"]
                                for kind in ("serial", "parallel"))
            print(f"{pair} {variant}: {serial}/{parallel} sixteenths, "
                  f"serial/parallel = {float(Fraction(serial, parallel)):.9f}")


def measure(bundle, client, binaries, compiler):
    sdk = bundle / "dynamorio"
    tools = {"launcher": sdk / "bin64/drrun", "runtime": sdk / "lib64/release/libdynamorio.so",
             "meter": client, "production_reader": ROOT / "collect.py"}
    data = {"schema": 1, "scope": "post-hoc local software counters; no native timing",
            "protocol_sha256": sha(PLAN_BYTES), "tools": {k: sha(v.read_bytes()) for k, v in tools.items()},
            "compiler": subprocess.check_output([compiler, "--version"], text=True).splitlines()[0],
            "linker": subprocess.check_output(["ld", "--version"], text=True).splitlines()[0],
            "source_sha256": {v: sha(source_for(v).encode()) for v in SUBSTITUTION}, "cases": {}}
    require(data["tools"] == PLAN["tool_sha256"], "expected the pinned measurement tools")
    with TemporaryDirectory() as tmp:
        base = Path(tmp)
        for case in DIV_CASES:
            name = case["workload_id"]
            data["cases"][name] = {}
            for variant in SUBSTITUTION:
                work = base / name / variant
                records = work / "records"
                records.mkdir(parents=True)
                binary = binaries / name / "Main"
                if variant == "mov":
                    # An explicit object name also fixes GNU ld's nonloaded STT_FILE symbol.
                    source, obj, binary = work / "kernels.S", work / "kernels.o", work / "Main"
                    source.write_text(source_for(variant))
                    subprocess.run([compiler, *case["gcc_arguments"], "-c", source, "-o", obj], check=True)
                    subprocess.run([compiler, *case["gcc_arguments"], obj, "-o", binary], check=True)
                elf_hash = sha(binary.read_bytes())
                require(variant != "xor" or elf_hash == case["binary_sha256"], "original ELF differs")
                loop = loop_listing(binary)
                check_disassembly(loop, case, variant)
                cmd = PRIOR["metered"](tools, work, binary, "operand_bytes")
                with subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE) as proc:
                    try:
                        stdout, stderr = proc.communicate(timeout=120)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()
                        raise
                require(not stderr and proc.returncode == 0 and stdout.hex() == case["stdout_hex"], "guest failed")
                stream = records / f"process-{proc.pid}.jsonl"
                require(list(records.iterdir()) == [stream], "expected one process stream")
                counts = PRIOR["production"](records, proc.pid, "operand_bytes", READER)
                data["cases"][name][variant] = {
                    "binary_sha256": elf_hash, "hot_loop": loop, "stdout_hex": stdout.hex(),
                    "exit_code": proc.returncode, "pid": proc.pid, "record_stream": stream.read_text(),
                    "cycles_x16": counts["latency_bound"]["cycles_x16"],
                }
    verify(data)
    return data


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--client", type=Path)
    parser.add_argument("--binaries", type=Path, help="directory of original archived <case>/Main ELFs")
    parser.add_argument("--compiler", default="gcc")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--output", type=Path)
    mode.add_argument("--verify", type=Path)
    args = parser.parse_args()
    if args.verify:
        verify(json.loads(args.verify.read_bytes()))
    elif args.bundle and args.client and args.binaries:
        data = measure(args.bundle.resolve(), args.client.resolve(), args.binaries.resolve(), args.compiler)
        with args.output.open("x") as out:
            out.write(json.dumps(data, indent=2) + "\n")
    else:
        parser.error("--output requires --bundle, --client and --binaries")
