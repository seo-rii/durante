#!/usr/bin/env python3
"""Resolve the twelve measured workloads from their previously published raw archives."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import tarfile

HERE = Path(__file__).resolve().parent
EXPERIMENTS = HERE.parent
ROOT = EXPERIMENTS.parent
GROUPS = [
    ("gce-dependency-pairs/followup", ["load_adjacent_serial", "load_adjacent_parallel",
                                       "load_scattered_serial", "load_scattered_parallel"], None),
    ("gce-rust-instruction-mix", ["integer", "copy", "branch", "sort", "fill"], "rust_instruction_mix/corpus.json"),
    ("gce-rust-instruction-mix/holdout", ["gcd", "lower_bound", "rolling_window"],
     "rust_instruction_mix/holdout/fixtures.json"),
]


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def fixtures(output_dir=None):
    cases = []
    archives = {}
    for archive_name, names, corpus in GROUPS:
        folder = EXPERIMENTS / "results" / archive_name
        dataset = json.loads(gzip.decompress((folder/"dataset.json.gz").read_bytes()))
        if corpus:
            programs = json.loads((EXPERIMENTS/corpus).read_bytes())["programs"]
            given = {}
            for name in names:
                case = next(c for c in programs[name]["cases"] if c["scale"] == 1)
                given[name] = (case["stdin"].encode(), case["stdout"].encode(), case["input_sha256"], case["expected_sha256"])
        else:
            manifest = json.loads((folder/"fixtures.json").read_bytes())
            given = {c["workload_id"]: (b"", bytes.fromhex(c["stdout_hex"]), c["input_sha256"], c["expected_sha256"])
                     for c in manifest["cases"]}
        archive = folder / "samples.tar.xz"
        relative = archive.relative_to(ROOT).as_posix()
        archives[relative] = sha(archive.read_bytes())
        with tarfile.open(archive, "r:xz") as tar:
            for name in names:
                member = tar.getmember(f"bin/{name}/Main")
                if not member.isfile() or member.size > 16*1024*1024:
                    raise ValueError(f"invalid archived ELF: {name}")
                raw = tar.extractfile(member).read()
                input_bytes, expected, input_sha, expected_sha = given[name]
                elf_hash = sha(raw)
                matches = [r for r in dataset["samples"] if r["workload_id"] == name and r.get("scale", 1) == 1]
                if (not raw.startswith(b"\x7fELF") or not matches or elf_hash != dataset["programs"][name]["binary_sha256"]
                        or sha(input_bytes) != input_sha or sha(expected) != expected_sha or
                        any(r["binary_sha256"] != elf_hash or r["input_sha256"] != input_sha or
                            r["expected_sha256"] != expected_sha for r in matches)):
                    raise ValueError(f"ELF or oracle disagrees with the archived observations: {name}")
                cases.append({"workload_id": name, "archive": relative, "binary_sha256": elf_hash,
                              "stdin_hex": input_bytes.hex(), "stdout_hex": expected.hex(),
                              "input_sha256": input_sha, "expected_sha256": expected_sha})
                if output_dir is not None:
                    dest = output_dir / name / "Main"
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    if dest.exists():
                        if dest.read_bytes() != raw:
                            raise ValueError(f"conflicting file at {dest}")
                    else:
                        dest.write_bytes(raw)
                        dest.chmod(0o700)
    return {"cases": cases, "archives_sha256": archives}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binaries", type=Path)
    args = parser.parse_args()
    print(json.dumps(fixtures(args.binaries), indent=2))
