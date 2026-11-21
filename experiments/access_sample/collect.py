#!/usr/bin/env python3
"""Collect CPU-pinned native and paired instrumented executions of archived ELFs."""
import argparse
import json
import os
from pathlib import Path
import runpy
import signal

from study import HERE, ROOT, read_sample, require, schedule, sha, validate_sample

previous = runpy.run_path(str(HERE.parent / "rust_operand_bytes/collect.py"))
shared = previous["shared"]


def run(args):
    protocol = HERE / "protocol.json"
    plan = json.loads(protocol.read_bytes())
    ordered = list(schedule(plan))
    binaries = args.binaries.resolve()
    bundle = args.bundle.resolve()
    tools = {"launcher": bundle / "dynamorio/bin64/drrun", "runtime": bundle / "dynamorio/lib64/release/libdynamorio.so",
             "meter": args.client.resolve(), "sampler": args.sampler.resolve(), "production_reader": ROOT / "collect.py"}
    sources = {k: ROOT/k for k in plan["sources_sha256"]}

    def check_files():
        require({k: shared.digest(v) for k, v in sources.items()} == plan["sources_sha256"], "changed measurement sources")
        require({k: shared.digest(v) for k, v in tools.items()} == plan["tool_sha256"], "changed measurement tools")
        for c in plan["cases"]:
            require(shared.digest(binaries/c["workload_id"]/"Main") == c["binary_sha256"], "changed workload ELF")

    require(args.cpu == 1 and os.sched_getaffinity(0) == {1}, "collector must be pinned to CPU 1")
    check_files()
    reader = runpy.run_path(str(tools["production_reader"]))["collect"]
    out = args.out.resolve()
    out.mkdir(exist_ok=True)
    require(not (out/"dataset.json").exists(), "use a fresh output")
    (out/"samples").mkdir()
    data = {"schema": 1, "complete": False, "collector_pid": os.getpid(), "cpu": args.cpu,
            "protocol_sha256": shared.digest(protocol), "collector_sha256": shared.digest(Path(__file__)),
            "sources_sha256": plan["sources_sha256"], "tools": plan["tool_sha256"],
            "programs": {c["workload_id"]: {"binary_sha256": c["binary_sha256"]} for c in plan["cases"]}, "samples": []}
    shared.save(out/"dataset.json", data)
    for index, (rep, case, phases) in enumerate(ordered, 1):
        name = case["workload_id"]
        binary = binaries/name/"Main"
        row = {k: case[k] for k in ("workload_id", "input_sha256", "expected_sha256", "binary_sha256")}
        row.update(repetition=rep, phase_order=phases)
        folder = out/"samples"/f"{index:02d}-{name}-{rep}"
        folder.mkdir()
        (folder/"stdin").write_bytes(bytes.fromhex(case["stdin_hex"]))
        for phase in phases:
            trial = folder/phase
            trial.mkdir()
            cmd = [binary]
            if phase in ("sampled", "baseline"):
                (trial/"records").mkdir()
                cmd = previous["metered"](tools, trial, binary, "operand_bytes")
                if phase == "sampled":
                    option = "-out " + shared.quoted(trial/"sample.json")
                    position = cmd.index("-follow_children")
                    cmd[position:position] = ["-client", tools["sampler"], "00000001", option]
            obs = shared.run_phase(cmd, trial, case["expected_sha256"], args.cpu)
            if phase in ("sampled", "baseline"):
                obs["production"] = previous["production"](trial/"records", obs["pid"], "operand_bytes", reader)
            if phase == "sampled":
                raw = (trial/"sample.json").read_bytes()
                sample = read_sample(raw)
                validate_sample(sample, obs["pid"])
                obs.update(sample=sample, sample_sha256=sha(raw))
            row[phase] = obs
        data["samples"].append(row)
        shared.save(out/"dataset.json", data)
    check_files()
    require(shared.digest(protocol) == data["protocol_sha256"], "protocol changed during acquisition")
    data["complete"] = True
    shared.save(out/"dataset.json", data)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("bundle", "client", "sampler", "binaries", "out"):
        parser.add_argument("--"+option, type=Path, required=True)
    parser.add_argument("--cpu", type=int, required=True)
    signal.signal(signal.SIGALRM, shared.timed_out)
    run(parser.parse_args())
