#!/usr/bin/env python3
"""Collect a native/diagnostic/native triplet for each scheduled dependency-pair case."""
import argparse
import json
import os
from pathlib import Path
import runpy
import signal

from study import HERE, ROOT, PHASES, require, schedule

previous = runpy.run_path(str(HERE.parent / "rust_operand_bytes/collect.py"))
shared = previous["shared"]


def run(args):
    protocol = HERE / "protocol.json"
    plan = json.loads(protocol.read_bytes())
    ordered = list(schedule(plan))
    binaries = args.binaries.resolve()
    bundle = args.bundle.resolve()
    tools = {"launcher": bundle / "dynamorio/bin64/drrun", "runtime": bundle / "dynamorio/lib64/release/libdynamorio.so",
             "meter": args.client.resolve(), "production_reader": ROOT / "collect.py"}
    paths = {k: ROOT / k for k in plan["sources_sha256"]}

    def verify_files():
        require({k: shared.digest(v) for k, v in tools.items()} == plan["tool_sha256"], "measurement tools changed")
        require({k: shared.digest(v) for k, v in paths.items()} == plan["sources_sha256"], "experiment sources changed")
        for c in plan["cases"]:
            require(shared.digest(binaries / c["workload_id"] / "Main") == c["binary_sha256"], "workload ELF changed")

    require(args.cpu == 1 and os.sched_getaffinity(0) == {1}, "collector must be pinned to CPU 1")
    verify_files()
    reader = runpy.run_path(str(tools["production_reader"]))["collect"]
    out = args.out.resolve()
    out.mkdir(exist_ok=True)
    require(not (out / "dataset.json").exists(), "use a fresh study output")
    (out / "samples").mkdir()
    data = {"schema": 1, "complete": False, "collector_pid": os.getpid(), "cpu": args.cpu,
            "protocol_sha256": shared.digest(protocol), "collector_sha256": shared.digest(Path(__file__)),
            "sources_sha256": plan["sources_sha256"], "tools": plan["tool_sha256"],
            "programs": {c["workload_id"]: {"binary_sha256": c["binary_sha256"]} for c in plan["cases"]},
            "samples": []}
    shared.save(out / "dataset.json", data)
    for index, (rep, case) in enumerate(ordered, 1):
        name = case["workload_id"]
        binary = binaries / name / "Main"
        row = {k: case[k] for k in ("workload_id", "binary_sha256", "input_sha256", "expected_sha256")}
        row.update(repetition=rep, phase_order=list(PHASES))
        folder = out / "samples" / f"{index:02d}-{name}-{rep}"
        folder.mkdir()
        (folder / "stdin").write_bytes(bytes.fromhex(case["stdin_hex"]))
        for phase in PHASES:
            trial = folder / phase
            trial.mkdir()
            command = [binary]
            if phase == "operand_bytes":
                (trial / "records").mkdir()
                command = previous["metered"](tools, trial, binary, phase)
            obs = shared.run_phase(command, trial, case["expected_sha256"], args.cpu)
            if phase == "operand_bytes":
                obs["production"] = previous["production"](trial / "records", obs["pid"], phase, reader)
            row[phase] = obs
        data["samples"].append(row)
        shared.save(out / "dataset.json", data)
    verify_files()
    require(shared.digest(protocol) == data["protocol_sha256"], "protocol changed during collection")
    data["complete"] = True
    shared.save(out / "dataset.json", data)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--client", type=Path, required=True)
    parser.add_argument("--binaries", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--cpu", type=int, required=True)
    signal.signal(signal.SIGALRM, shared.timed_out)
    run(parser.parse_args())
