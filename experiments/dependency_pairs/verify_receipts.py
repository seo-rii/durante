#!/usr/bin/env python3
"""Pack or independently replay the eight executables, inputs, outputs and final child receipts."""
import argparse
import io
import json
from pathlib import Path
import runpy
import tarfile
from tempfile import TemporaryDirectory

from study import HERE, ROOT, PHASES, load_protocol, read_json, require, sha, validated_rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--pack", type=Path, help="source directory of the guest's dataset and raw samples")
    parser.add_argument("--binaries", type=Path, help="original executable directory for packing")
    parser.add_argument("--reader", type=Path, help="original production reader for packing")
    args = parser.parse_args()
    if bool(args.pack) != bool(args.binaries) or bool(args.pack) != bool(args.reader):
        parser.error("--pack, --binaries and --reader must be supplied together")
    data = read_json(args.dataset)
    protocol = load_protocol(data)
    rows = list(validated_rows(data, protocol))
    cases = {c["workload_id"]: c for c in protocol["cases"]}
    names = ["tools/collect.py", *(f"bin/{name}/Main" for name in cases)]
    trials = []
    for index, row in rows:
        folder = f'samples/{index:02d}-{row["workload_id"]}-{row["repetition"]}'
        names.append(f"{folder}/stdin")
        for phase in PHASES:
            base = f"{folder}/{phase}"
            obs = row[phase]
            names.extend((f"{base}/phase.json", f"{base}/stdout"))
            record = f'process-{obs["pid"]}.jsonl' if phase == "operand_bytes" else None
            if record:
                names.append(f"{base}/records/{record}")
                if args.pack:
                    require([p.name for p in (args.pack/base/"records").iterdir()] == [record],
                            "unexpected diagnostic streams")
            trials.append((base, obs, record, cases[row["workload_id"]]))

    if args.pack:
        require(read_json(args.pack/"dataset.json") == data, "different guest dataset")
        with tarfile.open(args.archive, "x:xz", preset=6) as archive:
            for name in names:
                if name.startswith("bin/"):
                    path = args.binaries / name.removeprefix("bin/")
                elif name == "tools/collect.py":
                    path = args.reader
                else:
                    path = args.pack / name
                raw = path.read_bytes()
                member = tarfile.TarInfo(name)
                member.size = len(raw)
                member.mode = 0o755 if name.startswith("bin/") else 0o644
                archive.addfile(member, io.BytesIO(raw))

    # The archive supplies data and inert build inputs. Replay uses the
    # repository's current production reader to check continued compatibility.
    reader = runpy.run_path(str(ROOT/"collect.py"))["collect"]
    project = runpy.run_path(str(HERE.parent/"rust_operand_bytes/collect.py"))["production"]
    with tarfile.open(args.archive, "r:xz") as archive:
        members = archive.getmembers()
        require(len(members) == len(names) and {m.name for m in members} == set(names) and
                all(m.isfile() and m.size <= 16*1024*1024 for m in members), "incorrect archive contents")

        def read(name):
            return archive.extractfile(name).read()

        require(sha(read("tools/collect.py")) == data["tools"]["production_reader"], "invalid original reader")
        for name, case in cases.items():
            elf = read(f"bin/{name}/Main")
            require(elf.startswith(b"\x7fELF") and sha(elf) == case["binary_sha256"], "wrong archived ELF: " + name)
        for base, obs, record, case in trials:
            require(sha(read(base.rsplit("/", 1)[0]+"/stdin")) == case["input_sha256"], "wrong input")
            require(sha(read(base+"/stdout")) == obs["stdout_sha256"] == case["expected_sha256"], "wrong stdout")
            require(json.loads(read(base+"/phase.json")) == {k:v for k,v in obs.items() if k != "production"},
                    "wait4 receipt differs in " + base)
            if record:
                with TemporaryDirectory() as temp:
                    directory = Path(temp)
                    (directory/record).write_bytes(read(base+"/records/"+record))
                    measured = project(directory, obs["pid"], "operand_bytes", reader)
                    require(measured == obs["production"], "counter replay differs in " + base)
    print(f"Verified {len(cases)} archived ELFs, {len(rows)} inputs, {len(trials)} wait4 receipts and outputs, "
          f"and {len(rows)} original production records.")


if __name__ == "__main__":
    main()
