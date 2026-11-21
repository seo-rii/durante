#!/usr/bin/env python3
"""Bracket latency-only and memory-operand-byte observations on archived Rust executables."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import signal

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PREVIOUS = HERE.parent / "rust_instruction_mix"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


shared = load_module("rust_reference_measurement", PREVIOUS / "collect.py")


def load_cases(protocol, binaries):
    fixtures = {}
    for path in (PREVIOUS / "corpus.json", PREVIOUS / "holdout/fixtures.json"):
        for name, program in json.loads(path.read_bytes())["programs"].items():
            for case in program["cases"]:
                fixtures[name, case["scale"]] = (program["source_sha256"], case)
    cases = []
    programs = {}
    for planned in protocol["cases"]:
        name, scale = planned["workload_id"], planned["scale"]
        source, fixture = fixtures[name, scale]
        binary = binaries / name / "Main"
        shared.require(source == planned["source_sha256"] and
                       shared.digest(binary) == planned["binary_sha256"] and
                       shared.sha(fixture["stdin"].encode()) == fixture["input_sha256"] == planned["input_sha256"] and
                       shared.sha(fixture["stdout"].encode()) == fixture["expected_sha256"] == planned["expected_sha256"],
                       f"{name}/{scale}: archive, source, input or output digest mismatch")
        programs[name] = {"source_sha256": source, "binary_sha256": planned["binary_sha256"]}
        cases.append((name, fixture, binary))
    shared.require(len(cases) == len(fixtures) == 18 and
                   {(name, case["scale"]) for name, case, _ in cases} == set(fixtures), "wrong case set")
    return programs, cases


def metered(tools, folder, program, mode):
    shared.require(mode in ("latency_only", "operand_bytes"), "unknown diagnostic")
    options = f'-outdir {shared.quoted(folder / "records")} -rep_bytes -cpuid_profile x86-64-v2 -latency_bound'
    if mode == "operand_bytes":
        options += " -memory_operand_bytes"
    return [tools["launcher"], "-quiet", "-client", tools["meter"], "00000000", options,
            "-follow_children", "-disable_traces", "-vm_size", "32M", "-vmheap_size", "32M", "--", program]


def production(records, pid, mode, aggregate):
    receipt = aggregate(records, pid)
    shared.require(receipt.get("complete") is True and receipt["valid"] is True and
                   receipt["root_pid"] == pid and receipt["quality"] == "uninterrupted" and
                   receipt["cpuid_profile"] == "x86-64-v2" and receipt["signals_observed"] == 0 and
                   receipt["process_count"] == receipt["image_count"] == receipt["segments"][0]["threads"] == 1 and
                   receipt["processes"][0]["exit_code"] == 0 and receipt["unsupported_rep_entries"] == 0 and
                   receipt["rep_bytes_enabled"] is True and receipt["rep_breakdown"]["version"] == 1 and
                   receipt["latency_bound"]["version"] == 1 and receipt["latency_bound"]["cycles_x16"] > 0 and
                   ("memory_operand_bytes" in receipt) == (mode == "operand_bytes"), "invalid runtime receipt")
    keys = ("complete", "valid", "quality", "cpuid_profile", "signals_observed", "process_count", "image_count",
            "unsupported_rep_entries", "instructions", "rep_bytes", "latency_bound")
    result = {key: receipt[key] for key in keys}
    result.update(threads=receipt["segments"][0]["threads"], rep_movs_bytes=receipt["rep_breakdown"]["movs_bytes"],
                  rep_stos_bytes=receipt["rep_breakdown"]["stos_bytes"])
    if mode == "operand_bytes":
        memory = receipt["memory_operand_bytes"]
        shared.require(memory["version"] == 1 and type(memory["bytes"]) is int and memory["bytes"] > 0,
                       "missing operand-width observations")
        result["memory_operand_bytes"] = memory
    return result


def run(args):
    plan_path = HERE / "protocol.json"
    plan = json.loads(plan_path.read_bytes())
    shared.require(plan["round_rotations"] == [0, 6, 12] and plan["repetitions"] == 3, "wrong frozen schedule")
    shared.require(os.sched_getaffinity(0) == {args.cpu}, "collector must be pinned to the benchmark CPU")
    out = args.out.resolve()
    out.mkdir(exist_ok=True)
    shared.require(not (out / "dataset.json").exists(), "use an empty study output")
    (out / "samples").mkdir()

    reader_path = ROOT / "collect.py"
    reader = load_module("durante_runtime_collector", reader_path)
    bundle = args.bundle.resolve()
    tools = {"launcher": bundle / "dynamorio/bin64/drrun", "runtime": bundle / "dynamorio/lib64/release/libdynamorio.so",
             "meter": args.client.resolve(), "production_reader": reader_path}
    digests = {name: shared.digest(path) for name, path in tools.items()}
    programs, cases = load_cases(plan, args.binaries.resolve())
    data = {"schema": 1, "complete": False, "cpu": args.cpu, "collector_pid": os.getpid(),
            "protocol_sha256": shared.digest(plan_path), "collector_sha256": shared.digest(Path(__file__)),
            "shared_collector_sha256": shared.digest(PREVIOUS / "collect.py"), "tools": digests,
            "programs": programs, "samples": []}
    shared.save(out / "dataset.json", data)

    for round_index, offset in enumerate(plan["round_rotations"]):
        for step in range(len(cases)):
            index = (offset + step) % len(cases)
            name, fixture, binary = cases[index]
            scale = fixture["scale"]
            round_number = round_index + 1
            modes = ["latency_only", "operand_bytes"]
            if (round_index + index) % 2:
                modes.reverse()
            order = ["native_before", *modes, "native_after"]
            row = {"workload_id": name, "scale": scale, "repetition": round_number, **programs[name],
                   "input_sha256": fixture["input_sha256"], "expected_sha256": fixture["expected_sha256"],
                   "phase_order": order}
            folder = out / "samples" / f'{len(data["samples"])+1:02d}-{name}-{scale}-{round_number}'
            folder.mkdir()
            (folder / "stdin").write_bytes(fixture["stdin"].encode())
            for phase in order:
                trial = folder / phase
                trial.mkdir()
                command = [binary]
                if phase in modes:
                    (trial / "records").mkdir()
                    command = metered(tools, trial, binary, phase)
                obs = shared.run_phase(command, trial, fixture["expected_sha256"], args.cpu)
                if phase in modes:
                    obs["production"] = production(trial / "records", obs["pid"], phase, reader.collect)
                row[phase] = obs
                shared.save(folder / "sample.json", row)
            data["samples"].append(row)
            shared.save(out / "dataset.json", data)
    shared.require({name: shared.digest(path) for name, path in tools.items()} == digests, "tool changed during run")
    shared.require(all(shared.digest(args.binaries / name / "Main") == info["binary_sha256"]
                       for name, info in programs.items()), "archived executable changed during run")
    shared.require(len(data["samples"]) == 54, "incomplete measurement")
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
