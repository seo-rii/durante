#!/usr/bin/env python3
"""Replay exact program binaries, outputs, wait4, canonical records and DIV descriptors."""
import argparse
import gzip
import io
import json
from pathlib import Path
import tarfile
from tempfile import TemporaryDirectory

from study import MODES, ROOT, plan, production, require, rows, sha, validate


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


def trials(data):
    inputs, observations, names = {}, [], []
    for n,(row, case, phases) in enumerate(rows(data), 1):
        prefix = f"samples/{n:02d}-{case['workload_id']}-{row['repetition']}"
        inputs[prefix+"/stdin"] = bytes.fromhex(case["stdin_hex"])
        names.append(prefix+"/stdin")
        for mode in phases:
            trial,obs = prefix+"/"+mode,row[mode]
            names.extend(trial+"/"+s for s in ("phase.json","stdout","stderr"))
            record = f"process-{obs['pid']}.jsonl" if mode in MODES else None
            if record:
                names.append(trial+"/records/"+record)
            if mode == "sampled":
                names.append(trial+"/sample.json")
            observations.append((trial,obs,mode,record,bytes.fromhex(case["stdout_hex"])))
    return names,inputs,observations


def verify(dataset, archive, raw_dir=None):
    data = read_dataset(dataset)
    check_fixtures(plan())
    names, inputs, observations = trials(data)
    if raw_dir is not None:
        require(read_dataset(raw_dir/"dataset.json") == data, "archive source dataset differs")
        with tarfile.open(archive, "x:xz", preset=6) as output:
            for name in names:
                path = raw_dir/name
                raw = path.read_bytes()
                member = tarfile.TarInfo(name)
                member.size = len(raw)
                output.addfile(member, io.BytesIO(raw))
        for trial,_,mode,record,_ in observations:
            folder = raw_dir/trial
            require((folder/"sample.json").exists() == (mode == "sampled"), "extra sidecar")
            require(sorted(p.name for p in (folder/"records").glob("*")) == ([record] if record else []),
                    "extra process stream")
    with tarfile.open(archive) as tar:
        members = tar.getmembers()
        require(len(members) == len(names) and {m.name for m in members} == set(names) and
                all(m.isfile() and m.size < 4*1024*1024 for m in members), "unexpected archived records")
        def read(name):
            return tar.extractfile(name).read()
        for name, expected in inputs.items():
            require(read(name) == expected, "wrong stdin")
        for trial,obs,mode,record,expected in observations:
            require(read(trial+"/stdout") == expected and read(trial+"/stderr") == b"", "wrong child output")
            require(json.loads(read(trial+"/phase.json")) == {k:v for k,v in obs.items() if k not in
                    ("production","sample","sample_sha256")}, "wait4 record disagrees")
            if record:
                with TemporaryDirectory() as tmp:
                    location = Path(tmp)
                    (location/record).write_bytes(read(trial+"/records/"+record))
                    require(production(mode,location,obs["pid"]) == obs["production"], "wrong production projection")
            if mode == "sampled":
                raw = read(trial+"/sample.json")
                sample = json.loads(raw)
                validate(sample,obs["pid"])
                require(sha(raw) == obs["sample_sha256"] and sample == obs["sample"], "wrong DIV sidecar")
    print("Verified 4 ELF digests, 12 quintets, 60 child receipts/outputs, 36 process records and 12 DIV reports.")


if __name__=="__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("dataset", type=Path)
    cli.add_argument("archive", type=Path)
    cli.add_argument("--pack", type=Path, help="directory containing the original guest receipts")
    args = cli.parse_args()
    verify(args.dataset, args.archive, args.pack)
