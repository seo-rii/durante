#!/usr/bin/env python3
"""Build matched divide/load chains, calculate their checksums and verify their ELF loops."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess

SOURCE = Path(__file__).resolve().with_name("kernels.S")
WORD_MASK = (1 << 64) - 1
DIV_OPERATIONS = 1 << 25
# Four lanes cover four consecutive quarters of the same stream. The common
# load count traverses both entire tables and gives each lane a distinct seed.
LOAD_OPERATIONS = 17 * (1 << 22) + 4096
PAIRS = {
    "div_small": {"operations": DIV_OPERATIONS, "addend": 257},
    "div_large": {"operations": DIV_OPERATIONS, "addend": ((1 << 30) // 251) * 251 + 257},
    "load_l1": {"operations": LOAD_OPERATIONS, "entries": 4096},
    "load_large": {"operations": LOAD_OPERATIONS, "entries": 1 << 22},
}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def position(params, steps):
    """State after advancing a single reference stream by this many operations."""
    if "addend" in params:
        return (1 + steps * params["addend"]) % 251
    mask = params["entries"] - 1
    shift = mask.bit_length() // 2
    value = (steps * 0x9e3779b1) & mask
    value ^= value >> shift
    value = (value * 0x85ebca6b) & mask
    return value ^ (value >> shift)


def rotate(value):
    return ((value << 13) | (value >> 51)) & WORD_MASK


def oracle(params, lanes):
    """Each independent lane takes the next quarter of the serial stream."""
    operations = params["operations"]
    stride = operations // 4
    checksum = 0x21a93f5c7de10648
    for quarter in range(1, 5):
        checksum = rotate(checksum ^ position(params, quarter * stride // lanes))
    for lane in (1, 2, 3):
        end = lane * stride + (stride if lanes == 4 else 0)
        checksum ^= position(params, end)
        if lane != 3:
            checksum = rotate(checksum)
    return struct.pack("<Q", checksum)


def loop_listing(binary):
    symbols = {}
    for line in subprocess.check_output(["nm", "-n", binary], text=True).splitlines():
        fields = line.split()
        if len(fields) == 3 and fields[2] in ("hot_begin", "hot_end"):
            symbols[fields[2]] = int(fields[0], 16)
    listing = subprocess.check_output(
        ["objdump", "-d", "--no-show-raw-insn", f"--start-address={symbols['hot_begin']}",
         f"--stop-address={symbols['hot_end']}", binary], text=True)
    instructions = []
    for line in listing.splitlines():
        match = re.match(r"^\s*[0-9a-f]+:\s+([a-z0-9]+)\s*(.*)$", line)
        if match:
            op, operands = match.groups()
            instructions.append(op + " " + operands.strip())
    return instructions


def check_loop(listing, load, lanes):
    """Verify the built instructions and actual register-carried dependencies."""
    expected = []
    for lane in ([8, 9, 10, 11] if lanes == 4 else [8] * 4):
        if load:
            expected.append(f"mov 0x0(%rbp,%r{lane},4),%r{lane}d")
        else:
            expected.extend([f"mov %r{lane}d,%eax", "add %edi,%eax", "xor %edx,%edx",
                             "div %ebx", f"mov %edx,%r{lane}d"])
    if listing[:-2] != expected or listing[-2] != "dec %ecx" or not re.fullmatch(
            r"jne [0-9a-f]+ <hot_begin>", listing[-1]):
        raise ValueError(f"unexpected measured loop: {listing}")


def build(out, compiler):
    out.mkdir(parents=True, exist_ok=True)
    source_hash = sha(SOURCE.read_bytes())
    cases = []
    for pair, params in PAIRS.items():
        operations = params["operations"]
        assert operations % 16 == 0
        load = "entries" in params
        if load:
            assert operations >= params["entries"]
            assert len({position(params, lane * (operations // 4)) for lane in range(4)}) == 4
        for lanes, variant in ((1, "serial"), (4, "parallel")):
            name = f"{pair}_{variant}"
            folder = out / name
            folder.mkdir()
            binary = folder / "Main"
            defines = {"LOAD": int(load), "LANES": lanes, "ITERATIONS": operations // 16}
            if load:
                mask = params["entries"] - 1
                defines.update(MASK=mask, SHIFT=mask.bit_length() // 2)
            else:
                defines["ADDEND"] = params["addend"]
            for lane in range(4):
                defines[f"START{lane}"] = position(params, lane * (operations // 4))
            args = ["-static", "-nostdlib", "-no-pie", "-Wl,--build-id=none",
                    *(f"-D{key}={value}" for key, value in defines.items())]
            subprocess.run([compiler, *args, SOURCE, "-o", binary], check=True)
            loop = loop_listing(binary)
            check_loop(loop, load, lanes)
            expected = oracle(params, lanes)
            actual = subprocess.run([binary], input=b"", capture_output=True, check=True, timeout=60)
            if actual.stdout != expected or actual.stderr:
                raise ValueError(f"{name} native checksum mismatch")
            # Count the full ELF path, including table construction, slice
            # checkpoints, the checksum store and the two output/exit syscalls.
            instructions = (16 * params["entries"] + 30 + 6 * (operations // 4) if load
                            else 22 * (operations // 4) + 44)
            operand_bytes = 4 * (params["entries"] + operations) + 8 if load else 8
            cases.append({"workload_id": name, "pair": pair, "lanes": lanes, **params,
                          "expected_instructions": instructions, "expected_operand_bytes": operand_bytes,
                          "source_sha256": source_hash, "binary_sha256": sha(binary.read_bytes()),
                          "input_sha256": sha(b""), "expected_sha256": sha(expected),
                          "stdout_hex": expected.hex(), "gcc_arguments": args, "hot_loop": loop})
    return {"kernel_source_sha256": source_hash, "fixture_builder_sha256": sha(Path(__file__).read_bytes()),
            "compiler": subprocess.check_output([compiler, "--version"], text=True).splitlines()[0],
            "linker": subprocess.check_output(["ld", "--version"], text=True).splitlines()[0],
            "cases": cases}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", type=Path, required=True)
    parser.add_argument("--compiler", default="gcc")
    args = parser.parse_args()
    result = build(args.build, args.compiler)
    (args.build / "fixtures.json").write_text(json.dumps(result, indent=2) + "\n")
    print(f"Verified {len(result['cases'])} native outputs and decoded hot loops.")
