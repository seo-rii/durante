#!/usr/bin/env python3
"""Package or replay each child receipt, stdout and production record in the GCE comparison."""
import argparse
import gzip
import io
import json
from pathlib import Path
import runpy
import tarfile
from tempfile import TemporaryDirectory

from evaluate import HERE, children, require, sha


def load_dataset(path):
    raw = path.read_bytes()
    return json.loads(gzip.decompress(raw) if raw.startswith(b"\x1f\x8b") else raw)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--pack", type=Path, help="collector output from the guest")
    parser.add_argument("--reader", type=Path, help="path to the production reader used on the guest when packing")
    args = parser.parse_args()
    data = load_dataset(args.dataset)
    protocol = (HERE/"protocol.json").read_bytes()
    require(data["protocol_sha256"] == sha(protocol), "wrong protocol")
    plan = json.loads(protocol)
    trials = []
    names = ["tools/collect.py"]
    for i, row in children(data, plan):
        folder = f'samples/{i:02d}-{row["workload_id"]}-{row["scale"]}-{row["repetition"]}'
        for mode in row["phase_order"]:
            base = f"{folder}/{mode}"
            obs = row[mode]
            names.extend([base+"/phase.json", base+"/stdout"])
            record = f'process-{obs["pid"]}.jsonl' if mode in ("operand_bytes", "latency_only") else None
            if record:
                names.append(f"{base}/records/{record}")
                if args.pack:
                    require([x.name for x in (args.pack/base/"records").iterdir()] == [record], "extra raw stream")
            trials.append((base, mode, obs, record, row["expected_sha256"]))
    if args.pack:
        require(args.reader and sha(args.reader.read_bytes()) == data["tools"]["production_reader"],
                "measured production reader required")
        with args.archive.open("xb") as output, \
             gzip.GzipFile(fileobj=output, mode="wb", filename="", mtime=0) as compressed, \
             tarfile.open(fileobj=compressed, mode="w|") as archive:
            for name in names:
                raw = (args.reader if name == "tools/collect.py" else args.pack/name).read_bytes()
                member = tarfile.TarInfo(name)
                member.size = len(raw)
                archive.addfile(member, io.BytesIO(raw))
    # Replay through the repository's current reader and acquisition checks. Keep
    # the original reader as a digest-checked archival source file.
    parse = runpy.run_path(str(HERE.parents[1]/"collect.py"))["collect"]
    projection = runpy.run_path(str(HERE/"collect.py"))["production"]
    with tarfile.open(args.archive, "r:gz") as archive:
        members = archive.getmembers()
        require(len(members) == len(names) and set(names) == {m.name for m in members} and
                all(m.isfile() and m.size <= 1 << 20 for m in members), "unexpected archive entries")
        def read(name):
            return archive.extractfile(name).read()
        require(sha(read("tools/collect.py")) == data["tools"]["production_reader"], "original reader digest mismatch")
        for base, mode, obs, record, expected_stdout in trials:
            require(sha(read(base+"/stdout")) == obs["stdout_sha256"] == expected_stdout, "stdout mismatch")
            require(json.loads(read(base+"/phase.json")) == {k:v for k,v in obs.items() if k != "production"},
                    "wait4 record mismatch in "+base)
            if record:
                with TemporaryDirectory() as tmp:
                    folder = Path(tmp)
                    (folder/record).write_bytes(read(f"{base}/records/{record}"))
                    require(projection(folder, obs["pid"], mode, parse) == obs["production"],
                            "production counters disagree in "+base)
    print(f"Verified {len(trials)} wait4 receipts and outputs and {sum(r[3] is not None for r in trials)} production records.")


if __name__ == "__main__":
    main()
