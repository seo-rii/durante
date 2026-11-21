#!/usr/bin/env python3
"""Acquire native, baseline, DIV feature and fast static-clock observations on guest CPU 1."""
import argparse
import json
import os
from pathlib import Path
import signal

from study import HERE, MODES, PLAN, ROOT, plan, prior, production, require, rows, schedule, shared, validate


def collect(args):
    value = plan()
    bundle = args.bundle.resolve() / "dynamorio"
    tools = {"launcher": bundle/"bin64/drrun", "runtime": bundle/"lib64/release/libdynamorio.so",
             "meter": args.client.resolve(), "sampler": args.sampler.resolve(), "fast": args.fast.resolve(),
             "production_reader": ROOT/"collect.py"}
    binaries = {c["workload_id"]: args.binaries.resolve()/c["workload_id"]/"Main" for c in value["cases"]}

    def check_files():
        require({k: shared.digest(path) for k,path in tools.items()} == value["tool_sha256"], "changed library or reader")
        require(all(shared.digest(binaries[c["workload_id"]]) == c["binary_sha256"] for c in value["cases"]),
                "changed program ELF")

    require(args.cpu == 1 and os.sched_getaffinity(0) == {1}, "collector must run on guest CPU 1")
    check_files()
    out = args.out.resolve()
    out.mkdir(exist_ok=True)
    (out/"samples").mkdir()
    require(not (out/"dataset.json").exists(), "use fresh output")
    data = {"schema":1, "complete":False, "collector_pid":os.getpid(), "cpu":args.cpu,
            "protocol_sha256":shared.digest(PLAN), "collector_sha256":shared.digest(HERE/"collect.py"),
            "sources_sha256":value["sources_sha256"], "tools":value["tool_sha256"], "samples":[]}
    shared.save(out/"dataset.json", data)
    for index, (rep, case, phases) in enumerate(schedule(value), 1):
        row = {k:case[k] for k in ("workload_id", "binary_sha256", "input_sha256", "expected_sha256")}
        row.update(repetition=rep, phase_order=phases)
        binary = binaries[case["workload_id"]]
        folder = out/"samples"/f"{index:02d}-{case['workload_id']}-{rep}"
        folder.mkdir()
        (folder/"stdin").write_bytes(bytes.fromhex(case["stdin_hex"]))
        for mode in phases:
            trial = folder/mode
            trial.mkdir()
            cmd = [binary]
            if mode in MODES:
                (trial/"records").mkdir()
                active = dict(tools, meter=tools["fast"] if mode=="fast" else tools["meter"])
                cmd = prior["metered"](active, trial, binary, "operand_bytes")
                if mode == "sampled":
                    options = "-out " + shared.quoted(trial/"sample.json")
                    at = cmd.index("-follow_children")
                    cmd[at:at] = ["-client", tools["sampler"], "00000001", options]
            obs = shared.run_phase(cmd, trial, case["expected_sha256"], args.cpu)
            require(not (trial/"stderr").read_bytes(), "unexpected child stderr")
            if mode in MODES:
                obs["production"] = production(mode,trial/"records",obs["pid"])
            if mode == "sampled":
                raw = (trial/"sample.json").read_bytes()
                sample = json.loads(raw)
                validate(sample, obs["pid"])
                obs.update(sample=sample, sample_sha256=shared.sha(raw))
            row[mode] = obs
        data["samples"].append(row)
        shared.save(out/"dataset.json", data)
    check_files()
    data["complete"] = True
    list(rows(data))
    shared.save(out/"dataset.json", data)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("bundle","client","sampler","fast","binaries","out"):
        parser.add_argument("--"+option, type=Path, required=True)
    parser.add_argument("--cpu", type=int, required=True)
    signal.signal(signal.SIGALRM, shared.timed_out)
    collect(parser.parse_args())
