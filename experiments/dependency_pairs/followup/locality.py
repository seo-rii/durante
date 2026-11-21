#!/usr/bin/env python3
"""Build the two-field successor table control and verify its native checksum and ELF loops."""
import argparse
import hashlib
import json
from pathlib import Path
import runpy
import struct
import subprocess

HERE = Path(__file__).resolve().parent
base = runpy.run_path(str(HERE.parent / "fixtures.py"))
ENTRIES = 1 << 21
OPERATIONS = 33 * ENTRIES


def position(scattered, steps):
    if not scattered:
        return steps % ENTRIES
    return base["position"]({"entries": ENTRIES}, steps)


def checksum(scattered, lanes):
    quarter = OPERATIONS // 4
    value = 0x21a93f5c7de10648
    for i in range(1, 5):
        value = base["rotate"](value ^ position(scattered, i * quarter // lanes))
    for lane in (1, 2, 3):
        value ^= position(scattered, lane * quarter + (quarter if lanes == 4 else 0))
        if lane < 3:
            value = base["rotate"](value)
    return struct.pack("<Q", value)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def build(out):
    cases, initials, hot_loops = [], set(), {1: set(), 4: set()}
    source = HERE / "locality.S"
    for order in ("adjacent", "scattered"):
        scattered = order == "scattered"
        for lanes in (1, 4):
            name = f'load_{order}_{"serial" if lanes == 1 else "parallel"}'
            folder = out / name
            folder.mkdir(parents=True)
            starts = [position(scattered, i * OPERATIONS // 4) for i in range(4)]
            assert len(set(starts)) == 4
            defines = {"ENTRIES": ENTRIES, "MASK": ENTRIES-1, "SHIFT": 10, "OFFSET": 4 * int(scattered),
                       "LANES": lanes, "ITERATIONS": OPERATIONS // 16, **{f"START{i}": s for i,s in enumerate(starts)}}
            flags = [f"-D{k}={v}" for k,v in defines.items()]
            subprocess.run(["gcc", *flags, "-c", source, "-o", "locality.o"], cwd=folder, check=True)
            subprocess.run(["ld", "--build-id=none", "-o", "Main", "locality.o"], cwd=folder, check=True)
            binary = folder / "Main"
            raw = subprocess.check_output(["objcopy", "-O", "binary", "-j", ".text", binary, "/dev/stdout"])
            symbols = {parts[2]: int(parts[0], 16) for line in subprocess.check_output(
                ["nm", "-n", binary], text=True).splitlines() if len(parts := line.split()) == 3}
            def code(start, end):
                return raw[symbols[start]-symbols["_start"]:symbols[end]-symbols["_start"]]
            initials.add(code("_start", "init_end"))
            hot_loops[lanes].add(code("hot_begin", "hot_end"))
            listing = base["loop_listing"](binary)
            wanted = [f"mov 0x0(%rbp,%r{n},8),%r{n}d" for n in ([8]*4 if lanes == 1 else [8,9,10,11])]
            if listing[:-2] != wanted or listing[-2] != "dec %ecx" or not listing[-1].endswith("<hot_begin>"):
                raise ValueError("unexpected successor lookup instructions")
            expected = checksum(scattered, lanes)
            if subprocess.check_output([binary], input=b"", timeout=60) != expected:
                raise ValueError("native successor checksum mismatch")
            cases.append({"workload_id": name, "pair": "load_"+order, "lanes": lanes, "entries": ENTRIES,
                          "operations": OPERATIONS, "record_bytes": 8, "field_offset": defines["OFFSET"],
                          "expected_instructions": 19*ENTRIES + 46 + 6*(OPERATIONS//4),
                          "expected_operand_bytes": 8*ENTRIES + 4*OPERATIONS + 8,
                          "expected_sha256": sha(expected), "stdout_hex": expected.hex(), "input_sha256": sha(b""),
                          "binary_sha256": sha(binary.read_bytes()), "source_sha256": sha(source.read_bytes()),
                          "gcc_defines": flags, "hot_loop": listing, "init_code_sha256": sha(code("_start","init_end")),
                          "hot_code_sha256": sha(code("hot_begin","hot_end"))})
    if len(initials) != 1 or any(len(group) != 1 for group in hot_loops.values()):
        raise ValueError("initialization or matched hot code changed across fields")
    return cases


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    args.manifest.write_text(json.dumps(build(args.build.resolve()), indent=2) + "\n")
