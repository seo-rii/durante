#!/usr/bin/env python3
"""Generate matched order and loop-exit fixtures from the archived Rust GCD input."""
import argparse
from collections import Counter, defaultdict
import hashlib
import io
import json
from math import gcd
from pathlib import Path
import runpy
import struct
import subprocess
import tarfile
from tempfile import TemporaryDirectory

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
EXPERIMENTS = ROOT / "experiments"
DATA = EXPERIMENTS / "results/gce-gcd-controls/loops"
SOURCE = HERE / "gcd_loop.S"
ARCHIVE = DATA / "elfs.tar.xz"
MANIFEST = DATA / "fixtures.json"
VIEWS = ("original", "grouped")
MODES = ("remainder", "counted")
RECORD = struct.Struct("<III")


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def inputs():
    plan = json.loads((EXPERIMENTS / "access_sample/protocol.json").read_bytes())
    case = next(c for c in plan["cases"] if c["workload_id"] == "gcd")
    reference = EXPERIMENTS / "results/divide-clock/operand-profiles.json"
    oracle = json.loads(reference.read_bytes())["gcd"]
    stdin = bytes.fromhex(case["stdin_hex"])
    pair_count, seed = map(int, stdin.split())
    require(sha(stdin) == case["input_sha256"] and case["binary_sha256"] == oracle["binary_sha256"]
            and pair_count == oracle["input_pairs"] == 524_288, "different original GCD workload")
    random_words = runpy.run_path(str(EXPERIMENTS / "rust_instruction_mix/holdout/oracles.py"))["random_words"]
    words = random_words(seed)
    original, buckets = bytearray(), defaultdict(bytearray)
    lengths = []
    checksum = 0
    for _ in range(pair_count):
        a = max(1, next(words) & 0x7fffffff)
        b = max(1, next(words) & 0x7fffffff)
        x, y = a, b
        count = 0
        while y:
            x, y = y, x % y
            count += 1
        require(x == gcd(a, b), "wrong Euclidean result")
        checksum += x
        lengths.append(count)
        row = RECORD.pack(a, b, count)
        original.extend(row)
        buckets[count].extend(row)
    histogram = dict(sorted(Counter(lengths).items()))
    divides = sum(lengths)
    require({str(k): v for k,v in histogram.items()} == oracle["divisions_per_pair_histogram"]
            and divides == oracle["hot_divisions"] and checksum == oracle["checksum"]
            and f"{checksum}\n".encode().hex() == case["stdout_hex"], "wrong operands or trip counts")
    # Stable partition: every original record is appended once to its own length bucket.
    grouped = b"".join(buckets[k] for k in sorted(buckets))
    groups = {"original": bytes(original), "grouped": grouped}
    common = {"pairs": pair_count, "source_elf_sha256": case["binary_sha256"],
              "source_input_sha256": sha(stdin), "source_oracle_sha256": sha(reference.read_bytes()),
              "divisions": divides, "gcd_checksum": checksum, "inner_branch_taken": divides - pair_count,
              "inner_branch_fallthrough": pair_count, "record_format": "little-endian u32 a, b, trip_count",
              "trip_length_histogram": {str(k): v for k, v in histogram.items()}}
    orders = {}
    for name, table in groups.items():
        # Both lengths and adjacency counts are from the actual binary file order.
        counts = (r[2] for r in RECORD.iter_unpack(table))
        previous = next(counts)
        same = changes = 0
        for current in counts:
            same += current == previous
            changes += abs(current - previous)
            previous = current
        orders[name] = {"same_length_as_preceding_pair": same, "adjacent_pair_transitions": pair_count - 1,
                        "sum_absolute_length_change": changes}
    require(orders["grouped"]["same_length_as_preceding_pair"] == pair_count - len(histogram), "incorrect stable grouping")
    return groups, struct.pack("<QQ", checksum, divides), common, orders


def build(folder, mode):
    obj = folder / "gcd_loop.o"
    binary = folder / mode
    subprocess.run(["as", "--64", "--defsym", f"COUNTED={int(mode == 'counted')}", str(SOURCE), "-o", obj], check=True)
    subprocess.run(["ld", "-static", "-x", "--build-id=none", "-o", binary, obj], check=True)
    return binary.read_bytes()


def verify_code(elfs):
    remainder, counted = (elfs[name] for name in MODES)
    require(len(remainder) == len(counted) and remainder[:6] == b"\x7fELF\x02\x01", "different ELF layout")
    changed = [i for i, (a,b) in enumerate(zip(remainder, counted, strict=True)) if a != b]
    require(len(changed) == 1, "expected only the TEST register ModRM byte to differ")
    prefix = bytes.fromhex("89f8 89d7 31d2 f7f7 ffc9 85")
    suffix = bytes.fromhex("75 f2")
    start = changed[0] - len(prefix)
    stop = start + len(prefix) + 1 + len(suffix)
    for binary, reg in ((remainder, b"\xd2"), (counted, b"\xc9")):
        require(binary[start:stop] == prefix + reg + suffix, "unexpected hot-loop machine code")
    table = struct.unpack_from("<Q", remainder, 32)[0]
    size, count = struct.unpack_from("<HH", remainder, 54)
    for index in range(count):
        kind, flags, offset, addr, _, n = struct.unpack_from("<IIQQQQ", remainder, table+index*size)
        if kind == 1 and flags & 1 and offset <= start and stop <= offset+n:
            return {"function_vaddr": addr + start - offset, "changed_file_offset": changed[0],
                    "remainder_machine_code": remainder[start:stop].hex(), "counted_machine_code": counted[start:stop].hex()}
    raise ValueError("inner loop outside executable segment")


def archive_elfs(elfs):
    with tarfile.open(ARCHIVE, "x:xz") as tar:
        for name in MODES:
            obj = tarfile.TarInfo(f"bin/{name}/Main")
            obj.size = len(elfs[name])
            obj.mode = 0o755
            tar.addfile(obj, io.BytesIO(elfs[name]))


def read_elfs():
    with tarfile.open(ARCHIVE, "r:xz") as tar:
        members = tar.getmembers()
        require(len(members) == len(MODES) and {m.name for m in members} == {f"bin/{m}/Main" for m in MODES}
                and all(m.isfile() and 0 < m.size < 65536 for m in members), "unexpected executable archive")
        return {name: tar.extractfile(f"bin/{name}/Main").read() for name in MODES}


def check_and_write(path, raw, mode):
    if path.is_file():
        require(sha(path.read_bytes()) == sha(raw), f"conflicting existing output {path}")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as out:
            out.write(raw)
        path.chmod(mode)


def fixtures(publish, materialize, run):
    tables, stdout, common, orders = inputs()
    if publish:
        DATA.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory() as temp:
            elfs = {name: build(Path(temp), name) for name in MODES}
        verify_code(elfs)
        archive_elfs(elfs)
    elfs = read_elfs()
    plan = {"schema": 1, **common, "orders": orders, "code": verify_code(elfs),
            "assembly_sha256": sha(SOURCE.read_bytes()), "archive_sha256": sha(ARCHIVE.read_bytes()), "cases": []}
    for mode_name in MODES:
        for order in VIEWS:
            name = f"loop_{mode_name}_{order}"
            table, binary = tables[order], elfs[mode_name]
            filename = f"loop_{order}.bin"
            plan["cases"].append({"workload_id": name, "binary_sha256": sha(binary), "elf_member": f"bin/{mode_name}/Main",
                                  "input_file": filename, "input_sha256": sha(table), "input_size": len(table),
                                  "expected_sha256": sha(stdout), "stdout_hex": stdout.hex()})
            if materialize:
                path = materialize / "inputs" / filename
                dest = materialize / "elfs" / name / "Main"
                check_and_write(path, table, 0o600)
                check_and_write(dest, binary, 0o700)
                if run:
                    with path.open("rb") as fd:
                        result = subprocess.run([dest], stdin=fd, capture_output=True, check=True, timeout=60)
                    require(result.stdout == stdout and not result.stderr, f"incorrect native checksum: {name}")
    if publish:
        with MANIFEST.open("x") as out:
            out.write(json.dumps(plan, indent=2) + "\n")
    else:
        require(json.loads(MANIFEST.read_bytes()) == plan, "published fixtures differ from replay")
    print(f"Verified {len(plan['cases'])} loop controls, both input orders and the one-byte change of TEST operand.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publish", action="store_true", help="create the initial assembly ELF archive and fixture manifest")
    parser.add_argument("--materialize", type=Path, help="root for inputs/ and elfs/ directories")
    parser.add_argument("--run", action="store_true", help="execute the four materialized ELFs and verify checksums")
    args = parser.parse_args()
    if args.run and not args.materialize:
        parser.error("--run requires --materialize")
    fixtures(args.publish, args.materialize, args.run)
