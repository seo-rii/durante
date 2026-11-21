#!/usr/bin/env python3
"""Acquire native-bracketed version-9001 timings on a single guest CPU."""
import argparse
import json
import os
from pathlib import Path
import signal

from study import (CASE_KEYS, HERE, MODES, PLAN, ROOT, input_manifest, plan, prior, production,
                   read_inputs, require, rows, schedule, shared, validate_trip)


def collect(args):
    value = plan()
    bundle = args.bundle.resolve() / "dynamorio"
    tools = {"launcher": bundle/"bin64/drrun", "runtime": bundle/"lib64/release/libdynamorio.so",
             "fast": args.fast.resolve(), "trip_probe": args.trip_probe.resolve(), "production_reader": ROOT/"collect.py"}
    binaries = {c["workload_id"]: args.binaries.resolve()/c["workload_id"]/"Main" for c in value["cases"]}

    def check_tools():
        require({k: shared.digest(path) for k, path in tools.items()} == value["tool_sha256"],
                "changed library or reader")
        require(all(shared.digest(binaries[c["workload_id"]]) == c["binary_sha256"] for c in value["cases"]),
                "changed program ELF")

    require(args.cpu == 1 and os.sched_getaffinity(0) == {1}, "collector must run on guest CPU 1")
    check_tools()
    out = args.out.resolve()
    out.mkdir(exist_ok=True)
    require(not (out/"dataset.json").exists(), "use fresh output")
    (out/"samples").mkdir()
    inputs = args.inputs.resolve()
    expected_inputs = input_manifest(value)
    data = {"schema": 1, "complete": False, "collector_pid": os.getpid(), "cpu": args.cpu,
            "protocol_sha256": shared.digest(PLAN), "collector_sha256": shared.digest(HERE/"collect.py"),
            "sources_sha256": value["sources_sha256"], "tools": value["tool_sha256"], "samples": [],
            "inputs_before": read_inputs(inputs, expected_inputs)}
    shared.save(out/"dataset.json", data)
    for index, (rep, case, phases) in enumerate(schedule(value), 1):
        row = {k: case[k] for k in CASE_KEYS}
        row.update(repetition=rep, phase_order=phases)
        binary = binaries[case["workload_id"]]
        folder = out/"samples"/f"{index:02d}-{case['workload_id']}-{rep}"
        folder.mkdir()
        (folder/"stdin").symlink_to(inputs/case["input_file"])
        for phase in phases:
            trial = folder/phase
            trial.mkdir()
            cmd = [binary]
            if phase in MODES:
                (trial/"records").mkdir()
                cmd = prior["metered"](dict(tools, meter=tools["fast"]), trial, binary, "operand_bytes")
                if phase == "trips":
                    at = cmd.index("-follow_children")
                    cmd[at:at] = ["-client", tools["trip_probe"], "00000001",
                                  "-out " + shared.quoted(trial/"trip.json")]
            obs = shared.run_phase(cmd, trial, case["expected_sha256"], args.cpu)
            require((trial/"stderr").read_bytes() == b"", "unexpected child stderr")
            if phase in MODES:
                obs["production"] = production(trial/"records", obs["pid"])
            if phase == "trips":
                raw = (trial/"trip.json").read_bytes()
                trip = json.loads(raw)
                validate_trip(trip, obs["pid"])
                obs.update(trip=trip, trip_sha256=shared.sha(raw))
            row[phase] = obs
        data["samples"].append(row)
        shared.save(out/"dataset.json", data)
    check_tools()
    data["inputs_after"] = read_inputs(inputs, expected_inputs)
    data["complete"] = True
    list(rows(data))
    shared.save(out/"dataset.json", data)


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    for name in ("bundle", "fast", "trip-probe", "binaries", "inputs", "out"):
        cli.add_argument("--"+name, type=Path, required=True)
    cli.add_argument("--cpu", type=int, required=True)
    signal.signal(signal.SIGALRM, shared.timed_out)
    collect(cli.parse_args())
