#!/usr/bin/env python3
"""Compare the two loop-recurrence collectors on one static Linux x86-64 executable."""
import argparse
import json
import os
import platform
import statistics
from pathlib import Path

from collect_load_model import quoted, run_with_meter
from reference_features import require
from time_features import digest


def compare(args):
    tools = {key: getattr(args, key).resolve() for key in ("drrun", "meter", "probe")}
    program, stdin, oracle = (path.resolve() for path in (args.program, args.stdin, args.oracle))
    sdk = tools["drrun"].parents[1]
    inputs = {**tools, "release_core": sdk / "lib64/release/libdynamorio.so",
              "drmgr": sdk / "ext/lib64/release/libdrmgr.so",
              "program": program, "stdin": stdin, "oracle": oracle}
    hashes = {key: digest(path) for key, path in inputs.items()}
    output = args.output.resolve()
    output.mkdir(mode=0o700)
    config = output / "register"
    config.mkdir(mode=0o700)
    prefix = ("env", f"DYNAMORIO_CONFIGDIR={config}", "setarch", "-R")
    os.sched_setaffinity(0, {args.cpu})
    oracle_bytes = oracle.read_bytes()
    timings = {"full": [], "recurrence_only": []}
    reference = None
    for index in range(args.pairs):
        modes = list(timings)
        if index % 2:
            modes.reverse()
        for mode in modes:
            folder = output / f"{index + 1}-{mode}"
            report = folder / "features.json"
            options = "-out " + quoted(report)
            if mode == "recurrence_only":
                options += " -loop_recurrence_only"
            _, cpu_ns, meter = run_with_meter(tools, program, stdin, oracle_bytes, folder,
                                              options, args.timeout, prefix=prefix)
            features = json.loads(report.read_bytes())
            recurrence = features["loop_recurrence_cycles_x16"]
            require(type(recurrence) is int and 0 <= recurrence < 2**64, "invalid recurrence")
            if mode == "full":
                require(features["bpred"] is False and features["instructions"] == meter["instructions"],
                        "full probe and production instructions disagree")
            else:
                require(type(features.get("version")) is int and
                        features == {"version": 1, "loop_recurrence_cycles_x16": recurrence},
                        "invalid sparse probe report")
            counters = {"instructions": meter["instructions"], "rep_bytes": meter["rep_bytes"],
                        "latency_bound_x16": meter["latency_bound"]["cycles_x16"],
                        "loop_recurrence_cycles_x16": recurrence}
            if reference is None:
                reference = counters
            require(counters == reference, f"counter mismatch in {folder.name}: {counters} vs {reference}")
            timings[mode].append(cpu_ns)
    require(hashes == {key: digest(path) for key, path in inputs.items()}, "input changed during measurement")
    cpu_model = next((line.split(":", 1)[1].strip() for line in Path("/proc/cpuinfo").read_text().splitlines()
                      if line.startswith("model name")), "unknown")
    result = {"schema": 1, "cpu": args.cpu, "cpu_model": cpu_model, "kernel": platform.release(),
              "sha256": hashes, "pairs": args.pairs, "counters": reference, "child_cpu_ns": timings,
              "median_cpu_ns": {mode: statistics.median(values) for mode, values in timings.items()}}
    (output / "comparison.json").write_text(json.dumps(result, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("drrun", "meter", "probe", "program", "stdin", "oracle", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--cpu", required=True, type=int)
    parser.add_argument("--pairs", type=int, default=6)
    parser.add_argument("--timeout", type=int, default=180, help="seconds per child")
    args = parser.parse_args()
    if args.pairs <= 0 or args.timeout <= 0:
        parser.error("pairs and timeout must be positive")
    compare(args)


if __name__ == "__main__":
    main()
