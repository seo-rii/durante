#!/usr/bin/env python3
"""Run the three prospective Rust inputs with full and recurrence-only instrumentation."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess


HERE = Path(__file__).resolve().parent


def load_module(name, source):
    spec = importlib.util.spec_from_file_location(name, source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


shared = load_module("rust_mix_shared_collector", HERE.parent / "collect.py")


def read_fast(run, pid, aggregate):
    receipt = aggregate(run / "records", pid)
    shared.save(run / "production.json", receipt)
    shared.require(receipt.get("complete") is True and receipt["valid"] is True and
                   receipt["root_pid"] == pid and receipt["quality"] == "uninterrupted" and
                   receipt["cpuid_profile"] == "x86-64-v2" and receipt["signals_observed"] == 0 and
                   receipt["process_count"] == receipt["image_count"] == receipt["segments"][0]["threads"] == 1 and
                   receipt["processes"][0]["exit_code"] == 0 and receipt["unsupported_rep_entries"] == 0 and
                   receipt["rep_bytes_enabled"] is True and receipt["rep_breakdown"]["version"] == 1,
                   "invalid ordinary receipt in recurrence-only mode")
    production = {k: receipt[k] for k in ("complete", "valid", "instructions", "rep_bytes", "signals_observed",
                  "quality", "unsupported_rep_entries", "process_count", "image_count", "cpuid_profile")}
    production.update(threads=receipt["segments"][0]["threads"],
                      rep_movs_bytes=receipt["rep_breakdown"]["movs_bytes"],
                      rep_stos_bytes=receipt["rep_breakdown"]["stos_bytes"])
    report = json.loads((run / "features.json").read_bytes())
    shared.require(set(report) == {"version", "loop_recurrence_cycles_x16"} and
                   type(report["version"]) is int and report["version"] == 1 and
                   type(report["loop_recurrence_cycles_x16"]) is int and
                   0 <= report["loop_recurrence_cycles_x16"] < 2**64, "invalid recurrence-only report")
    return production, report


def run(args):
    protocol_path = HERE / "protocol.json"
    protocol = json.loads(protocol_path.read_bytes())
    fixture_path = HERE / "fixtures.json"
    fixture = json.loads(fixture_path.read_bytes())
    programs = protocol["programs"]
    shared.require(shared.digest(fixture_path) == protocol["fixtures_sha256"] and
                   programs == list(fixture["programs"]) == ["gcd", "lower_bound", "rolling_window"] and
                   protocol["round_rotations"] == [0, 1, 2], "holdout fixture or schedule mismatch")
    for name in programs:
        item = fixture["programs"][name]
        shared.require(len(item["cases"]) == 1 and item["cases"][0]["scale"] == 1, "invalid case count")
        case = item["cases"][0]
        shared.require(shared.sha(case["stdin"].encode()) == case["input_sha256"] and
                       shared.sha(case["stdout"].encode()) == case["expected_sha256"] and
                       shared.digest(HERE / (name + ".rs")) == item["source_sha256"], "fixture byte mismatch")
    shared.require(os.sched_getaffinity(0) == {args.cpu}, "collector must be pinned")
    out = args.out.resolve()
    out.mkdir(exist_ok=True)
    shared.require(not (out / "dataset.json").exists(), "output directory already used")
    (out / "bin").mkdir()
    (out / "samples").mkdir()
    bundle = args.bundle.resolve()
    tools = {"launcher": bundle / "dynamorio/bin64/drrun", "meter": bundle / "lib/libdurante.so",
             "runtime": bundle / "dynamorio/lib64/release/libdynamorio.so",
             "production_reader": HERE.parent / "production/collect.py",
             "probe": HERE.parent / "libtime_features_client.so"}
    reader = load_module("durante_production_reader", tools["production_reader"])
    hashes = {k: shared.digest(v) for k, v in tools.items()}
    shared.require(hashes == protocol["tools"], "measurement tools differ from the frozen training study")
    rustc = shutil.which("rustc")
    version = subprocess.check_output([rustc, "--version"], text=True).strip()
    shared.require(version == fixture["compiler_version"], "unexpected Rust compiler")
    data = {"schema": 1, "complete": False, "cpu": args.cpu, "collector_pid": os.getpid(),
            "collector_sha256": shared.digest(Path(__file__)), "shared_collector_sha256": shared.digest(HERE.parent / "collect.py"),
            "protocol_sha256": shared.digest(protocol_path), "fixtures_sha256": shared.digest(fixture_path),
            "compiler": {"executable": rustc, "version": version, "flags": fixture["compiler_flags"]},
            "tools": hashes, "programs": {}, "samples": []}
    shared.save(out / "dataset.json", data)
    binaries = shared.compile_programs(fixture, HERE, out, rustc, data)
    for index, offset in enumerate(protocol["round_rotations"]):
        for name in programs[offset:] + programs[:offset]:
            case = fixture["programs"][name]["cases"][0]
            full_first = (index + programs.index(name)) % 2 == 0
            shared.require(full_first == (len(data["samples"]) in protocol["full_first_indices"]),
                           "mode order differs from protocol")
            modes = ["full", "fast"] if full_first else ["fast", "full"]
            phases = ["native_before", *modes, "native_after"]
            row = {"workload_id": name, "scale": 1, "repetition": index + 1, **data["programs"][name],
                   "input_sha256": case["input_sha256"], "expected_sha256": case["expected_sha256"],
                   "phase_order": phases}
            folder = out / "samples" / f'{len(data["samples"])+1:02d}-{name}-1-{index+1}'
            folder.mkdir()
            (folder / "stdin").write_text(case["stdin"])
            for phase in phases:
                run = folder / phase
                run.mkdir()
                if phase in modes:
                    (run / "records").mkdir()
                    command = shared.metered_command(tools, run, binaries[name])
                    if phase == "fast":
                        opt = command.index("00000001") + 1
                        command[opt] += " -loop_recurrence_only"
                else:
                    command = [binaries[name]]
                result = shared.run_phase(command, run, case["expected_sha256"], args.cpu)
                if phase in modes:
                    read = shared.feature_result if phase == "full" else read_fast
                    result["production"], result["features"] = read(run, result["pid"], reader.collect)
                row[phase] = result
                shared.save(folder / "triplet.json", row)
            data["samples"].append(row)
            shared.save(out / "dataset.json", data)
    shared.require(all(shared.digest(binaries[n]) == data["programs"][n]["binary_sha256"] for n in programs) and
                   all(shared.digest(v) == hashes[k] for k, v in tools.items()), "measurement inputs changed")
    shared.require(len(data["samples"]) == 9, "incomplete collection")
    data["complete"] = True
    shared.save(out / "dataset.json", data)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--cpu", type=int, required=True)
    signal.signal(signal.SIGALRM, shared.timed_out)
    run(parser.parse_args())
