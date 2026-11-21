#!/usr/bin/env python3
"""Replay the original Durante records and compare raw child receipts and ELF hashes."""
import argparse
import gzip
import hashlib
import runpy
import io
import json
from pathlib import Path
import tarfile
from tempfile import TemporaryDirectory

from collect import feature_result
from holdout.collect_holdout import read_fast
from sample_layout import sample_layout


def require(ok, why):
    if not ok:
        raise ValueError(why)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--pack", type=Path, help="create the archive from a completed collector output directory")
    parser.add_argument("--probe-build", type=Path, help="verify the measured research client and its source")
    args = parser.parse_args()
    dataset = json.loads(gzip.decompress(args.dataset.read_bytes()) if args.dataset.suffix == ".gz"
                         else args.dataset.read_bytes())
    layout = list(sample_layout(dataset))
    if args.probe_build:
        build = json.loads(args.probe_build.read_bytes())
        source = Path(__file__).resolve().parents[1] / "time_features/featclient.c"
        require(build["feature_dso_sha256"] == dataset["tools"]["probe"] and
                build["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest(),
                "research client differs from its build record")
    binaries = {f"bin/{name}/Main": prog["binary_sha256"] for name, prog in dataset["programs"].items()}
    names = list(binaries)
    for row, folder, phases in layout:
        for phase in phases:
            names.extend(f"{folder}/{phase}/{name}" for name in ("phase.json", "stdout"))
        for mode in phases[1:-1]:
            records_path = f"{folder}/{mode}/records"
            record = f'process-{row[mode]["pid"]}.jsonl'
            names.extend([f"{folder}/{mode}/features.json", f"{records_path}/{record}"])
            if args.pack:
                require([entry.name for entry in (args.pack / records_path).iterdir()] == [record],
                        "unexpected raw stream in " + records_path)
    if args.pack:
        with tarfile.open(args.archive, "x:xz", preset=6) as tar:
            for name in names:
                data = (args.pack / name).read_bytes()
                info = tarfile.TarInfo(name)
                info.size = len(data)
                info.mode = 0o755 if name in binaries else 0o644
                tar.addfile(info, io.BytesIO(data))

    root = Path(__file__).resolve().parents[2]
    snapshot = root / "experiments/results/gce-rust-instruction-mix/collect.py.gz"
    raw_reader = gzip.decompress(snapshot.read_bytes())
    version = hashlib.sha256(raw_reader).hexdigest()
    require(version == dataset["tools"]["production_reader"] ==
            "671a6c0bfc58f19c9c73b04bab78e7fcba88214a9eb31acb6da49adaa2cac59b",
            "archived production reader digest mismatch")
    with TemporaryDirectory() as tmp:
        historical_reader = Path(tmp) / "collect.py"
        historical_reader.write_bytes(raw_reader)
        readers = [runpy.run_path(str(historical_reader))["collect"]]
    current_reader = root / "collect.py"
    if hashlib.sha256(current_reader.read_bytes()).hexdigest() != version:
        readers.append(runpy.run_path(str(current_reader))["collect"])
    with tarfile.open(args.archive, "r:xz") as tar:
        members = tar.getmembers()
        require(len(names) == len(members) and set(names) == {m.name for m in members} and
                all(m.isfile() and m.size <= 16 * 1024 * 1024 for m in members), "unexpected archive members")

        def load(name):
            return tar.extractfile(name).read()

        for name, expected in binaries.items():
            binary = load(name)
            require(binary.startswith(b"\x7fELF") and hashlib.sha256(binary).hexdigest() == expected,
                    "ELF digest mismatch for " + name)
        for row, folder, phases in layout:
            for phase in phases:
                raw = json.loads(load(f"{folder}/{phase}/phase.json"))
                expected = {k: v for k, v in row[phase].items() if k not in ("features", "production")}
                require(raw == expected, "child wait4 receipt mismatch in " + folder)
                stdout = load(f"{folder}/{phase}/stdout")
                require(hashlib.sha256(stdout).hexdigest() == raw["stdout_sha256"] == row["expected_sha256"],
                        "workload output mismatch in " + folder)
            for mode in phases[1:-1]:
                pid = row[mode]["pid"]
                record = f"records/process-{pid}.jsonl"
                with TemporaryDirectory() as tmp:
                    target = Path(tmp)
                    (target / "records").mkdir()
                    for name in ("features.json", record):
                        (target / name).write_bytes(load(f"{folder}/{mode}/{name}"))
                    read_features = read_fast if mode == "fast" else feature_result
                    for collect_records in readers:
                        production, features = read_features(target, pid, collect_records)
                        require(production == row[mode]["production"] and features == row[mode]["features"],
                                "replayed counters differ in " + folder)
    nphases = sum(len(phases) for _, _, phases in layout)
    print(f"Verified {len(binaries)} ELFs, {nphases} child receipts and all raw counters.")


if __name__ == "__main__":
    main()
