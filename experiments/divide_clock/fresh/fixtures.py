"""Independent exact output and operation counts for the two prospective inputs."""
import argparse
import hashlib
import io
import json
from math import isqrt
from pathlib import Path
import subprocess
import tarfile


def advance(x):
    return (1664525 * x + 1013904223) % (1 << 32)


def results():
    state = 0x93a2bcb1
    checksum = operations = 0
    for _ in range(256):
        state = advance(state)
        n = (1 << 27) + state % (1 << 27)
        root = isqrt(n)
        # Count integer lattice points i*j <= n by reflection over i=j.
        checksum += 2 * sum(n // i for i in range(1, root + 1)) - root * root
        groups = root + n // (root + 1)
        operations += 2 * groups
    expected = {"floor_groups": (checksum, operations)}
    state = 0x73a91d2f
    subtotal = 0
    for _ in range(4096):
        dividend = advance(state)
        state = advance(dividend)
        subtotal += dividend // (state % (1 << 16) + 1)
    expected["batch_quotients"] = (8192 * subtotal, 8192 * 4096)
    return {name: {"stdout": f"{number}\n", "expected_sha256": hashlib.sha256(f"{number}\n".encode()).hexdigest(),
                   "source_divisions": divisions} for name, (number, divisions) in expected.items()}


HERE = Path(__file__).resolve().parent
DATA = HERE.parents[1] / "results/divide-clock/fresh"
SOURCE = HERE / "holdouts.rs"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def archive_fixtures(binaries):
    archive, manifest = DATA / "holdouts.tar.xz", DATA / "fixtures.json"
    oracles = results()
    dataset = {"source_sha256": sha(SOURCE.read_bytes()),
               "build": "rustc +1.94.0 --edition 2024 -O -C target-cpu=x86-64-v2 --cfg NAME --crate-name NAME experiments/divide_clock/fresh/holdouts.rs -o OUTPUT",
               "rustc_commit": "4a4ef493e3a1488c6e321570238084b38948f6db", "cases": {}}
    if binaries:
        DATA.mkdir(parents=True, exist_ok=True)
        compiler = subprocess.check_output(["rustc", "+1.94.0", "-vV"], text=True)
        if dataset["rustc_commit"] not in compiler:
            raise ValueError("wrong compiler for these binaries")
        with tarfile.open(archive,"x:xz") as tar:
            for name in oracles:
                raw = (binaries/name/"Main").read_bytes()
                entry = tarfile.TarInfo(name+"/Main")
                entry.size = len(raw)
                entry.mode = 0o755
                tar.addfile(entry,io.BytesIO(raw))
    with tarfile.open(archive,"r:xz") as tar:
        members = tar.getmembers()
        if len(members) != len(oracles) or {m.name for m in members} != {n+"/Main" for n in oracles}:
            raise ValueError("unexpected fixture archive members")
        for name, oracle in oracles.items():
            entry = tar.getmember(name+"/Main")
            if not entry.isfile() or not 0 < entry.size <= 16*1024*1024:
                raise ValueError("invalid fixture archive entry")
            raw = tar.extractfile(entry).read()
            if not raw.startswith(b"\x7fELF\x02\x01"):
                raise ValueError("expected a 64-bit little-endian ELF")
            dataset["cases"][name] = {**oracle, "binary_sha256": sha(raw), "stdin_sha256": sha(b"")}
    dataset["archive_sha256"] = sha(archive.read_bytes())
    if binaries:
        manifest.write_text(json.dumps(dataset,indent=2)+"\n")
    elif dataset != json.loads(manifest.read_bytes()):
        raise ValueError("recomputed fixture oracles or identities differ")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--pack", type=Path, help="directory containing the two built NAME/Main files")
    group.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.verify or args.pack:
        archive_fixtures(args.pack)
    else:
        print(json.dumps(results(), indent=2))
