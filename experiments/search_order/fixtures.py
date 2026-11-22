"""Rebuild, materialize and verify the pinned lower-bound workload fixtures."""
import argparse
from array import array
import hashlib
from io import BytesIO
import json
from pathlib import Path
import struct
import subprocess
import sys
import tarfile
from tempfile import TemporaryDirectory

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DATA = ROOT / "experiments/results/gce-search-order"
ARCHIVE = DATA / "elfs.tar.xz"
MANIFEST = DATA / "fixtures.json"
RUST_ARCHIVE = ROOT / "experiments/results/gce-rust-instruction-mix/holdout/samples.tar.xz"
RUST_FIXTURE = ROOT / "experiments/rust_instruction_mix/holdout/fixtures.json"
MAIN_KEYS, MAIN_QUERIES, MAIN_SEED = 262144, 1048576, 15485863
PERIOD = 1021
HEADER = struct.Struct("<QQ")
VARIANTS = (
    ("cmov_random", "cmov", "random"), ("cmov_sorted", "cmov", "sorted"),
    ("jcc_random", "jcc", "random"), ("jcc_sorted", "jcc", "sorted"),
    ("cmov_block32", "cmov", "block32"), ("jcc_block32", "jcc", "block32"),
    ("cmov_random_small", "cmov", "small"), ("cmov_random_large", "cmov", "large"),
    ("rust_original", "rust", "rust_original"), ("rust_new", "rust", "rust_new"),
)


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write_verified(path, raw, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        require(path.is_file() and not path.is_symlink() and path.read_bytes() == raw, f"conflicting {path}")
        return
    with path.open("xb") as output:
        output.write(raw)
    path.chmod(mode)


def little_endian_u32(items):
    data = array("I", items)
    require(data.itemsize == 4, "a four-byte unsigned int is required")
    if sys.byteorder == "big":
        data.byteswap()
    return data.tobytes()


def query_sequence(keys, queries, state):
    targets = []
    for _ in range(queries):
        state ^= (state << 13) & 0xffffffff
        state ^= state >> 17
        state ^= (state << 5) & 0xffffffff
        targets.append(state % (2 * keys + 1))
    return targets


def replay_one(keys, target):
    base, size = 0, keys
    while size > 1:
        half = size // 2
        candidate = base + half
        if 2 * candidate < target:
            base = candidate
        size -= half
    return base, base + (2 * base < target)


def input_oracle(keys, targets, seed, order):
    full, sampled = [0] * 61, [0] * 61
    previous_line = None
    checksum = 0
    for i, target in enumerate(targets):
        # Table[i] == 2*i. The final indexed CMP sees the greatest key
        # strictly below the target, or index zero for the two lowest targets.
        insertion = (target + 1) // 2
        terminal = max(0, insertion - 1)
        checksum += insertion
        line = terminal >> 4
        if previous_line is not None:
            width = (previous_line ^ line).bit_length()
            full[width] += 1
            # Pair zero-based iteration 0 with 1, 1021 with 1022, and so on.
            if i % PERIOD == 1:
                sampled[width] += 1
        if i % PERIOD in (0, 1):
            require(replay_one(keys, target) == (terminal, insertion), "terminal or insertion mismatch")
        previous_line = line
    for target in (0, 1, 2, 3, keys, 2 * keys - 2, 2 * keys - 1, 2 * keys):
        end = (target + 1) // 2
        require(replay_one(keys, target) == (max(0, end - 1), end), "boundary mismatch")
    queries = len(targets)
    require(sum(full) == queries - 1 and sum(sampled) == (queries + PERIOD - 2) // PERIOD,
            "miscounted transitions or sampling phase")
    return {
        "keys": keys, "queries": queries, "seed": seed, "order": order, "checksum": checksum,
        "inner_loads": (keys - 1).bit_length() * queries, "period": PERIOD, "sample_pairs": sum(sampled),
        "sample_suffix_sum": sum(k * n for k, n in enumerate(sampled)),
        "sampled_suffix_histogram": sampled, "full_pairs": sum(full),
        "full_suffix_sum": sum(k * n for k, n in enumerate(full)), "full_suffix_histogram": full,
    }


def material_inputs(directory=None):
    original = json.loads(RUST_FIXTURE.read_bytes())["programs"]["lower_bound"]["cases"][0]
    settings = {
        "random": (MAIN_KEYS, MAIN_QUERIES, MAIN_SEED), "sorted": (MAIN_KEYS, MAIN_QUERIES, MAIN_SEED),
        "block32": (MAIN_KEYS, MAIN_QUERIES, MAIN_SEED),
        "small": (4096, MAIN_QUERIES, 271828183), "large": (2097152, MAIN_QUERIES, 314159265),
        "rust_original": tuple(map(int, original["stdin"].split())), "rust_new": (100003, 1310720, 161803399),
    }
    all_cases = {}
    for order, (keys, queries, seed) in settings.items():
        targets = query_sequence(keys, queries, seed)
        ranked = sorted(targets)
        sorted_hash = sha(little_endian_u32(ranked))
        if order == "sorted":
            targets = ranked
        elif order == "block32":
            for i in range(0, queries, 32):
                targets[i:i + 32] = sorted(targets[i:i + 32])
            require(sorted(targets) == ranked, "block sorting changed the multiset")
        del ranked
        oracle = input_oracle(keys, targets, seed, order)
        oracle["sorted_targets_sha256"] = sorted_hash
        text = order.startswith("rust_")
        raw = f"{keys} {queries} {seed}\n".encode() if text else HEADER.pack(keys, queries) + little_endian_u32(targets)
        stdout = f"{oracle['checksum']}\n".encode() if text else struct.pack("<Q", oracle["checksum"])
        if order == "rust_original":
            require(raw.decode() == original["stdin"] and stdout.decode() == original["stdout"], "archived oracle mismatch")
        filename = order + (".in" if text else ".bin")
        if directory:
            write_verified(directory / filename, raw, 0o600)
        all_cases[order] = {"input_file": filename, "input_size": len(raw), "input_sha256": sha(raw),
                            "stdout_hex": stdout.hex(), "expected_sha256": sha(stdout), "oracle": oracle}
    required = [all_cases[k]["oracle"] for k in ("random", "sorted", "block32")]
    for field in ("checksum", "sorted_targets_sha256", "inner_loads"):
        require(len({case[field] for case in required}) == 1, f"order controls differ in {field}")
    return all_cases


def build(folder):
    binaries = {}
    for branch, name in enumerate(("cmov", "jcc")):
        obj, elf = folder / (name + ".o"), folder / name
        subprocess.run(["cc", "-x", "assembler-with-cpp", "-c", f"-DUSE_BRANCH={branch}", HERE / "search.S", "-o", obj], check=True)
        subprocess.run(["ld", "-static", "-x", "--build-id=none", "-o", elf, obj], check=True)
        binaries[name] = elf.read_bytes()
    return binaries


def publish_assembly():
    with TemporaryDirectory() as tmp:
        binaries = build(Path(tmp))
    DATA.mkdir(parents=True, exist_ok=True)
    with tarfile.open(ARCHIVE, "x:xz") as tar:
        for name, raw in binaries.items():
            entry = tarfile.TarInfo(name + "/Main")
            entry.size, entry.mode = len(raw), 0o755
            tar.addfile(entry, BytesIO(raw))


def read_program(archive, name):
    with tarfile.open(archive) as tar:
        member = tar.getmember(name)
        require(member.isfile() and 0 < member.size < 16 * 1024 * 1024, "unexpected archived ELF")
        return tar.extractfile(member).read()


def inspect(binary, program):
    require(binary[:6] == b'\x7fELF\x02\x01', "expected little-endian ELF64")
    offset = struct.unpack_from("<Q", binary, 32)[0]
    stride, count = struct.unpack_from("<HH", binary, 54)
    loads = []
    for i in range(count):
        kind, flags, off, va, _, size = struct.unpack_from("<IIQQQQ", binary, offset + i * stride)
        if kind == 1:
            loads.append((flags, off, va, size))
    base = min(va for _, _, va, _ in loads) & ~4095
    # The final comparison precedes ADC and checksum accumulation. Check inner
    # control flow independently so a branchless/branch mislabel cannot pass.
    tail = bytes.fromhex("3914814883d0004901c1")
    candidates = []
    for flags, off, va, size in loads:
        if flags & 1:
            body = binary[off:off + size]
            if program == "rust":
                pc = 0x15302
                start = pc + base - va
                if start >= 0 and body[start:start + len(tail)] == tail:
                    archived_inner = bytes.fromhex("4d89d349d1eb498d2c033914a9480f42c54d29da4983fa0177e6")
                    require(body[0x15350 + base - va:0x15350 + base - va + len(archived_inner)] == archived_inner,
                            "unexpected archived Rust search loop")
                    candidates.append(pc)
                continue
            decision = "480f42c5" if program == "cmov" else "73034889e8"
            backedge = "77e6" if program == "cmov" else "77e5"
            inner = bytes.fromhex("4d89d349d1eb4a8d2c183914a9" + decision + "4d29da4983fa01" + backedge)
            require(body.count(inner + tail) == 1, "changed matched search loop or final comparison")
            candidates.append(va - base + body.index(inner + tail) + len(inner))
    require(len(candidates) == 1, "expected one search terminal in the ELF")
    return candidates[0]


def fixtures(args):
    if args.publish:
        publish_assembly()
    programs = {}
    for name in ("cmov", "jcc", "rust"):
        archive, member = (RUST_ARCHIVE, "bin/lower_bound/Main") if name == "rust" else (ARCHIVE, name + "/Main")
        binary = read_program(archive, member)
        programs[name] = (binary, {"binary_archive": str(archive.relative_to(ROOT)), "archive_member": member,
                                   "binary_sha256": sha(binary), "terminal_offset": inspect(binary, name)})
    if args.rebuild:
        with TemporaryDirectory() as tmp:
            rebuilt = build(Path(tmp))
        require(all(programs[name][0] == binary for name,binary in rebuilt.items()), "rebuild differed")
    inputs_dir = args.inputs or (args.materialize / "inputs" if args.materialize else None)
    inputs = material_inputs(inputs_dir)
    cases = []
    for case, name, order in VARIANTS:
        binary, info = programs[name]
        row = {"workload_id": case, **info, **inputs[order]}
        cases.append(row)
        if args.materialize:
            elf = args.materialize / "elfs" / case / "Main"
            write_verified(elf, binary, 0o700)
            if args.run:
                with (inputs_dir / row["input_file"]).open("rb") as stdin:
                    child = subprocess.run([elf], stdin=stdin, capture_output=True, check=True, timeout=60)
                require(not child.stderr and child.stdout.hex() == row["stdout_hex"], "unexpected checksum for " + case)
    result = {"schema": 1, "assembly_sha256": sha((HERE / "search.S").read_bytes()), "cases": cases}
    if args.publish:
        with MANIFEST.open("x") as out:
            out.write(json.dumps(result, indent=2) + "\n")
    else:
        require(result == json.loads(MANIFEST.read_bytes()), "fixture replay differs from manifest")
    print("Verified ten search cases, three ELF identities, seven input files and both multiset reorderings.")


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--publish", action="store_true", help="create the initial ELF archive and manifest")
    cli.add_argument("--rebuild", action="store_true", help="compare both archived assembly programs with a fresh build")
    output = cli.add_mutually_exclusive_group()
    output.add_argument("--materialize", type=Path, help="generate both inputs/ and elfs/ under this directory")
    output.add_argument("--inputs", type=Path, help="generate only the inputs, directly in this directory")
    cli.add_argument("--run", action="store_true", help="verify each materialized executable's stdout")
    args = cli.parse_args()
    if args.run and not args.materialize:
        cli.error("--run requires --materialize")
    fixtures(args)
