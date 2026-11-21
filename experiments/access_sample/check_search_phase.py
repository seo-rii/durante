#!/usr/bin/env python3
"""Post-hoc exact address-order replay for the archived scale-one Rust lower_bound ELF.

From the repository root, run
    python3 experiments/access_sample/check_search_phase.py --dataset experiments/results/gce-access-sample/dataset.json.gz
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import struct
import tarfile

from study import HERE, ROOT, load_plan, read_json, read_sample, require, sha, validated_rows, validate_sample

INNER, FINAL = 0x1535A, 0x15302
PERIOD, ALTERNATE = 1024, 1021
RESIDUES = range(0, 64, 4)  # All possible u32-array base addresses modulo one line.


def verify_elf(case):
    with tarfile.open(ROOT / case["archive"], "r:xz") as archive:
        raw = archive.extractfile("bin/lower_bound/Main").read()
    require(sha(raw) == case["binary_sha256"] and raw[:6] == b"\x7fELF\x02\x01", "wrong archived executable")
    phoff = struct.unpack_from("<Q", raw, 32)[0]
    entsize, count = struct.unpack_from("<HH", raw, 54)
    # Byte-for-byte checks of the halving, indexed CMP / CMOVB, loop back edge,
    # and the final indexed CMP / ADC that computes the insertion index.
    patterns = {0x15350: bytes.fromhex("4d89d3 49d1eb 498d2c03 3914a9 480f42c5 4d29da 4983fa01 77e6 eb96"),
                FINAL: bytes.fromhex("391481 4883d000 4901c1")}
    for pc, code in patterns.items():
        for i in range(count):
            kind, flags, offset, start, _, size = struct.unpack_from("<IIQQQQ", raw, phoff + i*entsize)
            if kind == 1 and flags & 1 and start <= pc and pc + len(code) <= start + size:
                require(raw[offset + pc - start:offset + pc - start + len(code)] == code, "unexpected loop encoding")
                break
        else:
            raise ValueError("search loop lies outside the executable mapping")


class Transitions:
    def __init__(self):
        self.total = self.far = 0
        self.ambiguous = Counter()

    def add(self, a, b):
        self.total += 1
        distance = abs(a - b)
        if distance >= 32:  # 32 u32 elements span at least two 64-byte lines.
            self.far += 1
        elif distance > 16:
            self.ambiguous[min(a, b) % 16, distance] += 1

    def far_pairs(self, residue):
        return self.far + sum(count for (low, distance), count in self.ambiguous.items()
                              if (low + residue // 4) % 16 + distance >= 32)


class LoadSite:
    def __init__(self):
        self.loads = 0
        self.previous = None
        self.full, self.registered, self.alternate = Transitions(), Transitions(), Transitions()

    def visit(self, index):
        if self.previous is not None:
            self.full.add(self.previous, index)
            if self.loads % PERIOD == 1:
                self.registered.add(self.previous, index)
            if self.loads % ALTERNATE == 1:
                self.alternate.add(self.previous, index)
        self.previous = index
        self.loads += 1


def replay(case):
    raw = bytes.fromhex(case["stdin_hex"])
    require(sha(raw) == case["input_sha256"], "wrong scale-one input")
    count, queries, seed = map(int, raw.split())
    sites = {INNER: LoadSite(), FINAL: LoadSite()}
    checksum = 0
    for _ in range(queries):
        seed ^= (seed << 13) & 0xffffffff
        seed ^= seed >> 17
        seed ^= (seed << 5) & 0xffffffff
        target = seed % (2*count + 1)
        base, size = 0, count
        while size > 1:
            half = size >> 1
            mid = base + half
            sites[INNER].visit(mid)
            if 2*mid < target:
                base = mid
            size -= half
        sites[FINAL].visit(base)
        index = base + (2*base < target)
        require(index == (target+1)//2, "search did not return the exact insertion index")
        checksum += index
    stdout = f"{checksum}\n".encode()
    require(stdout.hex() == case["stdout_hex"] and sha(stdout) == case["expected_sha256"], "guest checksum mismatch")
    return sites, checksum


def compare(sample, sites):
    measured = {s["offset"]: s for s in sample["sites"]}
    possible = set(RESIDUES)
    for pc, site in sites.items():
        row = measured[pc]
        require(row["loads"] == site.loads and row["pairs"] == site.registered.total, "wrong load or sample count")
        possible = {r for r in possible if site.registered.far_pairs(r) == row["far_pairs"]}
    require(possible, "no common array alignment reproduces both measured sites")
    return sorted(possible)


def bounds(transitions):
    far = [transitions.far_pairs(r) for r in RESIDUES]
    return {"pairs": transitions.total, "far_min": min(far), "far_max": max(far),
            "far_percent_range": [round(100*n/transitions.total, 5) for n in [min(far), max(far)]]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--dataset", type=Path, help="Complete measured dataset, plain or gzip JSON")
    inputs.add_argument("--sidecar", type=Path, help="One sampler JSON from the same ELF and input")
    args = parser.parse_args()
    plan = json.loads((HERE / "protocol.json").read_bytes())
    case = next(c for c in plan["cases"] if c["workload_id"] == "lower_bound")
    verify_elf(case)
    sites, checksum = replay(case)
    if args.sidecar:
        sample = read_sample(args.sidecar.read_bytes())
        validate_sample(sample, sample["pid"])
        samples = [sample]
    else:
        data = read_json(args.dataset)
        samples = [row["sampled"]["sample"] for _, row in validated_rows(data, load_plan(data))
                   if row["workload_id"] == "lower_bound"]
        require(len(samples) == 3, "expected three measured lower_bound runs")
    matches = [compare(s, sites) for s in samples]
    result = {hex(pc): {"loads": site.loads, "full": bounds(site.full), "period_1024": bounds(site.registered),
                        "exploratory_period_1021": bounds(site.alternate)} for pc, site in sites.items()}
    print(json.dumps({"binary_sha256": case["binary_sha256"], "checksum": checksum, "sites": result,
                      "matching_base_residues_per_run": matches}, indent=2))


if __name__ == "__main__":
    main()
