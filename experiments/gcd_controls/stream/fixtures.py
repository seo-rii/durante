"""Generate and authenticate three one-byte-different GCD divide-stream controls."""
import argparse
from collections import Counter
import hashlib
import io
import json
from pathlib import Path
import runpy
import struct
import subprocess
import tarfile
from tempfile import TemporaryDirectory

HERE = Path(__file__).resolve().parent
EXPERIMENTS = HERE.parents[1]
DATA = EXPERIMENTS / "results/gce-gcd-controls/stream"
SEED_CASE = EXPERIMENTS / "access_sample/protocol.json"
REFERENCE = EXPERIMENTS / "results/divide-clock/operand-profiles.json"
WORD_SOURCE = EXPERIMENTS / "rust_instruction_mix/holdout/oracles.py"
# 01 /r is ADD r32 to r/m32. The register source stays EDX.
ROUTES = {"stream_independent": ("%ebx", 0xd3), "stream_dividend": ("%eax", 0xd0),
          "stream_divisor": ("%ecx", 0xd1)}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def input_oracle(output=None):
    prior = next(c for c in json.loads(SEED_CASE.read_bytes())["cases"] if c["workload_id"] == "gcd")
    reference = json.loads(REFERENCE.read_bytes())["gcd"]
    stdin = bytes.fromhex(prior["stdin_hex"])
    require(sha(stdin) == prior["input_sha256"] and prior["binary_sha256"] == reference["binary_sha256"],
            "original GCD program/input mismatch")
    pairs, seed = map(int, stdin.split())
    words = runpy.run_path(str(WORD_SOURCE))["random_words"](seed)
    digest = hashlib.sha256()
    pack = struct.Struct("<III").pack
    buffer = bytearray()
    previous = divisions = total = sum_gcd = 0
    histogram = Counter()

    def flush():
        digest.update(buffer)
        if output is not None:
            output.write(buffer)
        buffer.clear()

    for _ in range(pairs):
        a = max(1, next(words) & 0x7fffffff)
        b = max(1, next(words) & 0x7fffffff)
        depth = 0
        while b:
            buffer.extend(pack(a, b, previous))
            previous = a % b
            total += previous
            a, b = b, previous
            depth += 1
        divisions += depth
        sum_gcd += a
        histogram[depth] += 1
        if len(buffer) >= 1 << 20:
            flush()
    flush()
    require(previous == 0 and pairs == reference["input_pairs"] and divisions == reference["hot_divisions"] and
            {str(k): v for k, v in histogram.items()} == reference["divisions_per_pair_histogram"] and
            sum_gcd == reference["checksum"] and f"{sum_gcd}\n".encode().hex() == prior["stdout_hex"],
            "original GCD oracle mismatch")
    return {"filename": "stream.bin", "size_bytes": 12*divisions, "sha256": digest.hexdigest(),
            "layout": "little-endian u32 dividend, u32 divisor, u32 previous remainder (initial 0)",
            "original_binary_sha256": prior["binary_sha256"], "original_input_sha256": sha(stdin),
            "original_checksum": sum_gcd, "pairs": pairs, "divisions": divisions, "sum_remainders": total}


def build(folder, entry_count):
    for name, (register, _) in ROUTES.items():
        path = folder / name
        path.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory() as tmp:
            subprocess.run(["cc", "-c", f"-DADD_TO={register}", f"-DRECORDS={entry_count}",
                            str(HERE/"replay.S"), "-o", "stream.o"], cwd=tmp, check=True)
            subprocess.run(["ld", "--build-id=none", "-o", str((path/"Main").resolve()), "stream.o"],
                           cwd=tmp, check=True)


def code(raw, folder):
    binary = folder/"Main"
    binary.write_bytes(raw)
    symbols = {}
    for line in subprocess.check_output(["nm", "-n", str(binary)], text=True).splitlines():
        address, _, name = line.split()
        symbols[name] = int(address, 16)
    start, route, end = (symbols[k] for k in ("hot_begin","route_zero","hot_end"))
    text_addr, text_offset, text_size = None, None, None
    phoff = struct.unpack_from("<Q", raw, 32)[0]
    entsize, count = struct.unpack_from("<HH", raw, 54)
    for i in range(count):
        kind, flags, offset, va, _, size = struct.unpack_from("<IIQQQQ", raw, phoff + i*entsize)
        if kind == 1 and flags & 1 and va <= start < end <= va + size:
            text_addr, text_offset, text_size = va, offset, size
            break
    require(text_size is not None and start < route < end, "hot symbols outside executable segment")
    offset = text_offset + route - text_addr
    require(raw[offset] == 0x01, "route is not ADD r32 to r/m32")
    hot = raw[text_offset+start-text_addr:text_offset+end-text_addr]
    disasm = subprocess.check_output(["objdump", "-d", "--no-show-raw-insn", f"--start-address={start}",
                                      f"--stop-address={end}", str(binary)], text=True)
    listing = [" ".join(s.split(":",1)[1].split()) for s in disasm.splitlines()
               if ":" in s and s.split(":",1)[0].strip().isalnum()]
    return offset+1, hot.hex(), listing


def manifest(input_data, archive):
    cases = {}
    baseline = None
    stdout = struct.pack("<Q", input_data["sum_remainders"])
    with tarfile.open(archive, "r:xz") as tar, TemporaryDirectory() as tmp:
        members = tar.getmembers()
        names = {name+"/Main" for name in ROUTES}
        require(len(members) == len(names) and {m.name for m in members} == names, "wrong ELF archive")
        for name, (register, modrm) in ROUTES.items():
            entry = tar.getmember(name+"/Main")
            require(entry.isfile() and 0 < entry.size < 65536, "invalid static ELF member")
            raw = tar.extractfile(entry).read()
            require(raw[:6] == b"\x7fELF\x02\x01", "wrong ELF type")
            where, hot, listing = code(raw, Path(tmp))
            require(raw[where] == modrm, "wrong ADD encoding")
            expected = ["mov (%rsi),%eax", "mov 0x4(%rsi),%ecx", "xor 0x8(%rsi),%edx", "or %edx,%r15d",
                        "mov $0x0,%ebx", f"add %edx,{register}", "xor %edx,%edx", "div %ecx", "add %rdx,%r12",
                        "add $0xc,%rsi", "dec %r13"]
            require(listing[:-1] == expected and listing[-1].startswith("jne ") and listing[-1].endswith("<hot_begin>"),
                    f"wrong hot loop in {name}: {listing}")
            if baseline is None:
                baseline = raw
            else:
                require(len(baseline) == len(raw) and
                        [i for i,(a,b) in enumerate(zip(baseline,raw)) if a != b] == [where],
                        "variants differ outside the ADD destination")
            cases[name] = {"binary_sha256": sha(raw), "stdin_filename": input_data["filename"],
                           "input_sha256": input_data["sha256"], "stdout_hex": stdout.hex(), "expected_sha256": sha(stdout),
                           "hot_loop": listing, "hot_hex": hot, "route_modrm_file_offset": where}
    return {"source_sha256": sha((HERE/"replay.S").read_bytes()), "elf_archive_sha256": sha(archive.read_bytes()),
            "input": input_data, "cases": cases}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-input", type=Path, help="generate one new regular file for the three workloads")
    parser.add_argument("--build", type=Path, help="build the ELFs into DIR/NAME/Main and freeze their archive")
    parser.add_argument("--verify", action="store_true", help="replay the operand generator and validate the archived ELFs")
    args = parser.parse_args()
    if args.write_input:
        args.write_input.parent.mkdir(parents=True, exist_ok=True)
        with args.write_input.open("xb") as f:
            oracle = input_oracle(f)
    else:
        oracle = input_oracle()
    archive = DATA/"elfs.tar.xz"
    metadata = DATA/"fixtures.json"
    if args.build:
        build(args.build, oracle["divisions"])
        DATA.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive, "x:xz") as tar:
            for name in ROUTES:
                raw = (args.build/name/"Main").read_bytes()
                entry = tarfile.TarInfo(name+"/Main")
                entry.mode = 0o755
                entry.size = len(raw)
                tar.addfile(entry, io.BytesIO(raw))
        metadata.write_text(json.dumps(manifest(oracle,archive), indent=2)+"\n")
    if args.verify:
        require(manifest(oracle,archive) == json.loads(metadata.read_bytes()), "incorrect stream fixture manifest")
    elif not args.build and metadata.is_file():
        require(oracle == json.loads(metadata.read_bytes())["input"], "incorrect generated stream")
    print(json.dumps(oracle))


if __name__ == "__main__":
    main()
