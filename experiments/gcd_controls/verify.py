#!/usr/bin/env python3
"""Replay the exact archived ELFs, stdout/stderr, wait4 and both diagnostic streams."""
import argparse
import gzip
import io
import json
from pathlib import Path
import tarfile
from tempfile import TemporaryDirectory

from study import MODES, ROOT, input_manifest, plan, production, read_inputs, require, rows, sha, validate_trip


def read_dataset(path):
    raw = path.read_bytes()
    return json.loads(gzip.decompress(raw) if raw.startswith(b"\x1f\x8b") else raw)


def check_fixtures(value):
    archives = {c["binary_archive"] for c in value["cases"]}
    require(set(value["archives_sha256"]) == archives, "different executable archives")
    for name in sorted(archives):
        archive = ROOT/name
        require(sha(archive.read_bytes()) == value["archives_sha256"][name], "changed executable archive")
        with tarfile.open(archive) as tar:
            for case in (c for c in value["cases"] if c["binary_archive"] == name):
                member = tar.getmember(case["archive_member"])
                require(member.isfile() and member.size < 16*1024*1024 and
                        sha(tar.extractfile(member).read()) == case["binary_sha256"], "wrong ELF")


def verify(dataset, archive, raw_dir=None, inputs=None):
    data = read_dataset(dataset)
    value = plan()
    check_fixtures(value)
    if inputs:
        read_inputs(inputs, input_manifest(value))
    trials = []
    names = []
    for n, (row, case, phases) in enumerate(rows(data), 1):
        prefix = f"samples/{n:02d}-{case['workload_id']}-{row['repetition']}"
        if raw_dir:
            require((raw_dir/prefix/"stdin").readlink() == Path("/study/inputs")/case["input_file"],
                    "wrong guest stdin file")
        for mode in phases:
            trial, obs = prefix+"/"+mode, row[mode]
            names.extend(trial+"/"+s for s in ("phase.json", "stdout", "stderr"))
            record = f"process-{obs['pid']}.jsonl" if mode in MODES else None
            if record:
                names.append(trial+"/records/"+record)
            if mode == "trips":
                names.append(trial+"/trip.json")
            trials.append((trial, obs, mode, record, bytes.fromhex(case["stdout_hex"])))
    if raw_dir:
        require(read_dataset(raw_dir/"dataset.json") == data, "archive source dataset differs")
        with tarfile.open(archive, "x:xz", preset=6) as output:
            for name in names:
                raw = (raw_dir/name).read_bytes()
                member = tarfile.TarInfo(name)
                member.size = len(raw)
                output.addfile(member, io.BytesIO(raw))
        for trial, _, mode, record, _ in trials:
            folder = raw_dir/trial
            require((folder/"trip.json").exists() == (mode == "trips") and
                    sorted(p.name for p in (folder/"records").glob("*")) == ([record] if record else []),
                    "extra or missing diagnostic stream")
    with tarfile.open(archive) as tar:
        members = tar.getmembers()
        require(len(members) == len(names) and {m.name for m in members} == set(names) and
                all(m.isfile() and m.size < 4*1024*1024 for m in members), "unexpected archived records")
        def read(name):
            return tar.extractfile(name).read()
        for trial, obs, mode, record, expected in trials:
            require(read(trial+"/stdout") == expected and read(trial+"/stderr") == b"", "wrong child output")
            require(json.loads(read(trial+"/phase.json")) == {k:v for k,v in obs.items() if k not in
                    ("production","trip","trip_sha256")}, "wait4 record disagrees")
            if record:
                with TemporaryDirectory() as tmp:
                    path = Path(tmp)
                    (path/record).write_bytes(read(trial+"/records/"+record))
                    require(production(path, obs["pid"]) == obs["production"], "wrong meter projection")
            if mode == "trips":
                raw = read(trial+"/trip.json")
                sample = json.loads(raw)
                validate_trip(sample, obs["pid"])
                require(sha(raw) == obs["trip_sha256"] and sample == obs["trip"], "wrong trip sidecar")
    print("Verified 10 ELFs, 40 quartets, 160 wait4 receipts/outputs, 80 canonical streams and 40 trip reports.")


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("dataset", type=Path)
    cli.add_argument("archive", type=Path)
    cli.add_argument("--pack", type=Path, help="directory containing the original guest receipts")
    cli.add_argument("--inputs", type=Path, help="also hash the reconstructed regular-file inputs")
    args = cli.parse_args()
    verify(args.dataset, args.archive, args.pack, args.inputs)
