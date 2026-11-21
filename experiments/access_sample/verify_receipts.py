#!/usr/bin/env python3
"""Pack or replay the wait4 receipts, outputs and original production and sampler records."""
import argparse
import io
import json
from pathlib import Path
import runpy
import tarfile
from tempfile import TemporaryDirectory

from fixtures import fixtures
from study import HERE, ROOT, load_plan, read_json, read_sample, require, sha, validate_sample, validated_rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--pack", type=Path, help="guest result directory with the original raw receipts")
    parser.add_argument("--reader", type=Path, help="original production reader when packing")
    args = parser.parse_args()
    if bool(args.reader) != bool(args.pack):
        parser.error("--pack and --reader must be supplied together")
    data = read_json(args.dataset)
    plan = load_plan(data)
    origins = fixtures()
    require(origins["archives_sha256"] == plan["archives_sha256"], "pinned ELF archives changed")
    require(len(origins["cases"]) == len(plan["cases"]) and all(
        all(frozen[k] == v for k,v in actual.items())
        for actual, frozen in zip(origins["cases"], plan["cases"], strict=True)), "wrong archived ELF or oracle")
    names = ["tools/collect.py"]
    trials = []
    inputs = {}
    for index,row in validated_rows(data, plan):
        folder = f'samples/{index:02d}-{row["workload_id"]}-{row["repetition"]}'
        inputs[folder+"/stdin"] = row["input_sha256"]
        names.append(folder+"/stdin")
        for phase in row["phase_order"]:
            base = f"{folder}/{phase}"
            obs = row[phase]
            names.extend((base+"/phase.json", base+"/stdout"))
            record = f'process-{obs["pid"]}.jsonl' if "production" in obs else None
            if record:
                names.append(base+"/records/"+record)
                if args.pack:
                    require([p.name for p in (args.pack/base/"records").iterdir()] == [record], "extra process stream")
            if phase == "sampled":
                names.append(base+"/sample.json")
            elif args.pack:
                require(not (args.pack/base/"sample.json").exists(), "sample produced outside sampled mode")
            trials.append((base, phase, obs, record, row["expected_sha256"]))

    if args.pack:
        require(read_json(args.pack/"dataset.json") == data, "different source dataset")
        with tarfile.open(args.archive, "x:xz", preset=6) as tar:
            for name in names:
                raw = (args.reader if name == "tools/collect.py" else args.pack/name).read_bytes()
                member = tarfile.TarInfo(name)
                member.size = len(raw)
                tar.addfile(member, io.BytesIO(raw))
    reader = runpy.run_path(str(ROOT/"collect.py"))["collect"]
    project = runpy.run_path(str(HERE.parent/"rust_operand_bytes/collect.py"))["production"]
    with tarfile.open(args.archive, "r:xz") as tar:
        members = tar.getmembers()
        require(len(members) == len(names) and {m.name for m in members} == set(names) and
                all(m.isfile() and m.size <= 4*1024*1024 for m in members), "incorrect archive contents")

        def read(name):
            return tar.extractfile(name).read()

        require(sha(read("tools/collect.py")) == data["tools"]["production_reader"], "wrong original reader")
        for name, expected in inputs.items():
            require(sha(read(name)) == expected, "wrong input bytes")
        for base, phase, obs, record, expected_stdout in trials:
            require(sha(read(base+"/stdout")) == obs["stdout_sha256"] == expected_stdout, "stdout differs in "+base)
            require(json.loads(read(base+"/phase.json")) == {k:v for k,v in obs.items() if k not in
                    ("production", "sample", "sample_sha256")}, "wait4 receipt differs in "+base)
            if record:
                with TemporaryDirectory() as tmp:
                    folder = Path(tmp)
                    (folder/record).write_bytes(read(base+"/records/"+record))
                    require(project(folder, obs["pid"], "operand_bytes", reader) == obs["production"],
                            "production counters differ in "+base)
            if phase == "sampled":
                raw = read(base+"/sample.json")
                sample = read_sample(raw)
                validate_sample(sample, obs["pid"])
                require(sha(raw) == obs["sample_sha256"] and sample == obs["sample"],
                        "sampler counters differ in "+base)
    print(f"Verified {len(plan['cases'])} archived ELFs and oracles, {len(inputs)} inputs, {len(trials)} wait4 "
          f"receipts and outputs, {sum(bool(t[3]) for t in trials)} production streams, and {len(inputs)} sampler receipts.")


if __name__ == "__main__":
    main()
