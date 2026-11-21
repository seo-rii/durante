#!/usr/bin/env python3
"""Check exact single-process block-cost substitution and integer divide width coverage."""
import argparse
from collections import Counter
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

from fast_reader import PRIOR, READER, production
from replay import block_x16, require, validate

HERE = Path(__file__).resolve().parent


def checksum(with_u32):
    state = 73
    for result in [14] * (3 + with_u32) + [-14] * 4:
        for _ in range(17):
            state = (((state << 3) | (state >> 29)) + result) & 0xffffffff
    return state.to_bytes(4, "little")


def marked(args, folder, binary, expected, is_fast):
    records = folder / "records"
    records.mkdir(parents=True)
    cmd = PRIOR["metered"]({"launcher": args.drrun, "meter": args.fast if is_fast else args.meter},
                          folder, binary, "operand_bytes")
    feature_path = folder / "feature.json"
    if not is_fast:
        pos = cmd.index("-follow_children")
        cmd[pos:pos] = ["-client", args.probe, "00000001", "-out " + PRIOR["shared"].quoted(feature_path)]
    with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as child:
        output, err = child.communicate(timeout=30)
    require(child.returncode == 0 and output == expected and not err, "incorrect instrumented execution")
    if is_fast:
        return production(records, child.pid), None
    projection = PRIOR["production"](records, child.pid, "operand_bytes", READER["collect"])
    feature = json.loads(feature_path.read_bytes())
    validate(feature, child.pid)
    return projection, feature


def check(args, tmp, with_u32):
    binary = tmp / "widths"
    subprocess.run(["cc", "-nostdlib", "-static", "-Wl,--build-id=none", f"-DWITH_U32={int(with_u32)}",
                    HERE / "widths.S", "-o", binary], check=True)
    expected = checksum(with_u32)
    require(subprocess.check_output([binary], timeout=10) == expected, "native checksum mismatch")
    baseline, feature = marked(args, tmp / "dual", binary, expected, False)
    fast, _ = marked(args, tmp / "fast", binary, expected, True)
    rates = json.loads((HERE / "parameters.json").read_bytes())["models"]["latency_interval"]
    counts = Counter()
    delta = 0
    for block in feature["blocks"]:
        for op in block["ops"]:
            if op["div_bits"]:
                counts[op["opcode"], op["div_bits"]] += block["entries"]
        delta += block["entries"] * (block_x16(block, *rates) - block["v1_x16"])
    expected_counts = {(op, width): 17 for op in ("div", "idiv") for width in (8, 16, 32, 64)
                       if with_u32 or (op, width) != ("div", 32)}
    require(dict(counts) == expected_counts, "wrong divide count, signedness or width")
    require((delta != 0) == with_u32, "unexpected correction scope")
    require(fast["latency_bound"] == {"version": 9001, "cycles_x16": baseline["latency_bound"]["cycles_x16"] + delta},
            "fast cost does not equal the original cost plus the replayed correction")
    require({k: v for k, v in fast.items() if k != "latency_bound"} ==
            {k: v for k, v in baseline.items() if k != "latency_bound"}, "changed ordinary production counters")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("drrun", "meter", "probe", "fast"):
        parser.add_argument("--"+key, required=True, type=Path)
    args = parser.parse_args()
    with TemporaryDirectory() as tmp:
        for option in (False, True):
            trial = Path(tmp) / str(int(option))
            trial.mkdir()
            check(args, trial, option)
    print("Verified signed and unsigned 8/16/32/64-bit division, exact corrected costs and explicit capability 9001.")
