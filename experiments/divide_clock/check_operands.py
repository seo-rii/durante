#!/usr/bin/env python3
"""Replay the unsigned 32-bit divisions in the archived Rust GCD and #70 hot loops."""
import argparse
from collections import Counter
import json
from math import gcd
from pathlib import Path
import runpy
import struct
import tarfile
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[2]
EXPERIMENTS = ROOT / "experiments"
PAIRS = EXPERIMENTS / "dependency_pairs"
FIX = runpy.run_path(str(PAIRS / "fixtures.py"))
HOLDOUT = EXPERIMENTS / "rust_instruction_mix/holdout"
sha = FIX["sha"]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def member(archive, case):
    raw = archive.extractfile(f'bin/{case["workload_id"]}/Main').read()
    require(sha(raw) == case["binary_sha256"] and raw[:6] == b"\x7fELF\x02\x01", "wrong archived ELF")
    return raw


def check_code(raw, address, code):
    phoff = struct.unpack_from("<Q", raw, 32)[0]
    entsize, count = struct.unpack_from("<HH", raw, 54)
    for i in range(count):
        kind, flags, offset, start, _, size = struct.unpack_from("<IIQQQQ", raw, phoff+i*entsize)
        if kind == 1 and flags & 1 and start <= address and address+len(code) <= start+size:
            at = offset + address - start
            require(raw[at:at+len(code)] == code, f"unexpected instructions at {address:#x}")
            return
    raise ValueError("expected code in an executable segment")


def gcd_profile():
    plan = json.loads((EXPERIMENTS / "access_sample/protocol.json").read_bytes())
    case = next(c for c in plan["cases"] if c["workload_id"] == "gcd")
    fixture = json.loads((HOLDOUT / "fixtures.json").read_bytes())["programs"]["gcd"]
    require(sha((HOLDOUT / "gcd.rs").read_bytes()) == fixture["source_sha256"], "wrong GCD source")
    with tarfile.open(ROOT / case["archive"], "r:xz") as archive:
        raw = member(archive, case)
    # Two xorshift32 draws, then AND 0x7fffffff and CMP/ADC to clamp
    # each zero to one, feed EDI and EDX before the first DIV.
    check_code(raw, 0x14ff0, bytes.fromhex(
        "89d8 c1e00d 31d8 89c2 c1ea11 31c2 89d0 c1e005 31d0 89c7 81e7ffffff7f 83ff01 83d700 "
        "89c2 c1e20d 31c2 89d0 c1e811 31d0 89c3 c1e305 31c3 89da 81e2ffffff7f 83fa01 83d200"))
    # Move old divisor and remainder, zero EDX, DIV EDI, TEST EDX, JNE
    # back to 0x15040. The second JNE ends the enclosing input-pair loop.
    check_code(raw, 0x15040, bytes.fromhex("89f8 89d7 31d2 f7f7 85d2 75f4 48ffc6 89f8 4801c1 4c39f6 7597"))
    stdin = bytes.fromhex(case["stdin_hex"])
    require(sha(stdin) == case["input_sha256"] == fixture["cases"][0]["input_sha256"], "wrong GCD input")
    pairs, seed = map(int, stdin.split())
    words = runpy.run_path(str(HOLDOUT / "oracles.py"))["random_words"](seed)
    dividends, divisors, quotients = [0]*33, [0]*33, [0]*33
    chain_lengths = Counter()
    sum_gcd = zero_quotients = small_quotients = fixed_divisor = 0
    for _ in range(pairs):
        a = max(1, next(words) & 0x7fffffff)
        b = max(1, next(words) & 0x7fffffff)
        expected = gcd(a, b)
        chain = 0
        while b:
            q, remainder = divmod(a, b)
            dividends[a.bit_length()] += 1
            divisors[b.bit_length()] += 1
            quotients[q.bit_length()] += 1
            zero_quotients += q == 0
            small_quotients += q <= 3
            fixed_divisor += b == 251
            a, b = b, remainder
            chain += 1
        require(a == expected, "GCD replay disagrees with math.gcd")
        sum_gcd += a
        chain_lengths[chain] += 1
    output = f"{sum_gcd}\n".encode()
    require(output.hex() == case["stdout_hex"] and sha(output) == case["expected_sha256"] ==
            fixture["cases"][0]["expected_sha256"], "GCD checksum mismatch")
    count = sum(quotients)
    require(count == sum(dividends) == sum(divisors) == sum(k*v for k, v in chain_lengths.items()), "inconsistent totals")
    return {"binary_sha256": case["binary_sha256"], "input_pairs": pairs, "checksum": sum_gcd,
            "hot_divisions": count, "operand_width_bits": 32, "quotient_zero": zero_quotients,
            "quotient_at_most_three": small_quotients, "divisor_251": fixed_divisor,
            "dividend_bits": dividends, "divisor_bits": divisors, "quotient_bits": quotients,
            "divisions_per_pair_histogram": dict(sorted(chain_lengths.items())),
            "inner_test_instructions": count, "inner_jne_taken": count-pairs, "inner_jne_fallthrough": pairs,
            "outer_cmp_instructions": pairs, "outer_jne_taken": pairs-1, "outer_jne_fallthrough": 1}


def control_profiles():
    results = {}
    for suffix in ("", "followup"):
        plan = json.loads((PAIRS / suffix / "protocol.json").read_bytes())
        directory = EXPERIMENTS / "results/gce-dependency-pairs" / suffix
        require(sha((directory / "fixtures.json").read_bytes()) == plan["fixture_manifest_sha256"], "wrong fixture manifest")
        with tarfile.open(directory / "samples.tar.xz", "r:xz") as archive, TemporaryDirectory() as temp:
            binary = Path(temp) / "Main"
            for c in plan["cases"]:
                name = c["workload_id"]
                if not name.startswith("div_"):
                    continue
                raw = member(archive, c)
                binary.write_bytes(raw)
                loop = FIX["loop_listing"](binary)
                require(loop == c["hot_loop"], f"wrong loop in {name}")
                idiom = c.get("zeroing", "xor")
                normalized = [ins.replace("mov $0x0,%edx", "xor %edx,%edx") for ins in loop]
                FIX["check_loop"](normalized, False, c["lanes"])
                require(loop.count("mov $0x0,%edx") == (4 if idiom == "mov" else 0), "wrong high-half zeroing")
                operations, addend = c["operations"], c["addend"]
                seeds = [FIX["position"](c, lane*(operations//4)) for lane in range(4)]
                # At _start each ELF puts 251 in EBX, addend in EDI, and
                # partitions one stream into four contiguous quarters.
                setup = b"\xbb" + struct.pack("<I", 251) + b"\xbf" + struct.pack("<I", addend)
                for lane, seed in enumerate(seeds):
                    setup += bytes((0x41, 0xb8+lane)) + struct.pack("<I", seed)
                setup += bytes.fromhex("49bf4806e17d5c3fa921 41bd04000000 b9") + struct.pack("<I", operations//16)
                check_code(raw, struct.unpack_from("<Q", raw, 24)[0], setup)
                require(FIX["oracle"](c, c["lanes"]).hex() == c["stdout_hex"], "control checksum mismatch")
                # Nonzero addend mod prime 251 walks the full set of 251 remainders.
                require(gcd(addend, 251) == 1 and operations % 16 == 0, "wrong divider recurrence")
                full, tail = divmod(operations, 251)
                qbits = Counter()
                for index in range(251):
                    dividend = addend + FIX["position"](c, index)
                    qbits[(dividend//251).bit_length()] += full + (index < tail)
                results[name] = {"binary_sha256": c["binary_sha256"], "streams": c["lanes"], "zeroing": idiom,
                                 "hot_divisions": operations, "operand_width_bits": 32, "divisor": 251,
                                 "dividend_range": [addend, addend+250], "quotient_bit_histogram": dict(sorted(qbits.items())),
                                 "hot_jne_taken": operations//4-4, "hot_jne_fallthrough": 4}
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", type=Path, help="compare with saved JSON from this script")
    args = parser.parse_args()
    encoded = json.dumps({"gcd": gcd_profile(), "controls": control_profiles()}, indent=2) + "\n"
    if args.verify:
        require(json.loads(encoded) == json.loads(args.verify.read_bytes()), "saved operand replay differs")
        print("Verified exact GCD and synthetic divide operand profiles.")
    else:
        print(encoded, end="")


if __name__ == "__main__":
    main()
