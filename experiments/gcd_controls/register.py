#!/usr/bin/env python3
"""Register this study from its source fixtures and a built trip-probe shared object."""
import argparse
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DATA = ROOT / "experiments/results/gce-gcd-controls"
FRESH = ROOT / "experiments/divide_clock/fresh/protocol.json"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_bytes())


def register(probe):
    prior = read(FRESH)
    tools = {k: prior["tool_sha256"][k] for k in ("launcher", "runtime", "fast", "production_reader")}
    tools["trip_probe"] = digest(probe)
    cases = {}
    for case in prior["cases"]:
        name = case["workload_id"]
        if name in ("gcd", "floor_groups", "batch_quotients"):
            raw = bytes.fromhex(case["stdin_hex"])
            cases[name] = dict(case, input_file="gcd.in" if raw else "empty.in", input_size=len(raw))
    streams = read(DATA/"stream/fixtures.json")
    for name, case in streams["cases"].items():
        cases[name] = {k: case[k] for k in ("binary_sha256", "input_sha256", "stdout_hex", "expected_sha256")}
        cases[name].update(workload_id=name, binary_archive=str((DATA/"stream/elfs.tar.xz").relative_to(ROOT)),
                           archive_member=name+"/Main", input_file=case["stdin_filename"],
                           input_size=streams["input"]["size_bytes"])
    for row in read(DATA/"loops/fixtures.json")["cases"]:
        case = dict(row)
        case["archive_member"] = case.pop("elf_member")
        case["binary_archive"] = str((DATA/"loops/elfs.tar.xz").relative_to(ROOT))
        cases[case["workload_id"]] = case
    order = read(HERE/"evaluation-plan.json")["cases"]
    if len(cases) != 10 or len(order) != 10 or set(cases) != set(order):
        raise ValueError("mismatched evaluation and case registration")
    sources = {k for k in prior["sources_sha256"] if k.startswith("client/")}
    sources.update("""
        collect.py
        experiments/access_sample/protocol.json
        experiments/divide_clock/fast/CMakeLists.txt
        experiments/divide_clock/fast/fast.c
        experiments/divide_clock/fast_reader.py
        experiments/divide_clock/fresh/holdouts.rs
        experiments/divide_clock/fresh/protocol.json
        experiments/divide_clock/protocol.json
        experiments/results/divide-clock/fresh/evaluation.json
        experiments/results/divide-clock/fresh/fixtures.json
        experiments/results/divide-clock/operand-profiles.json
        experiments/results/gce-gcd-controls/loops/fixtures.json
        experiments/results/gce-gcd-controls/stream/fixtures.json
        experiments/results/gce-rust-operand-bytes/evaluation.json
        experiments/rust_instruction_mix/collect.py
        experiments/rust_instruction_mix/holdout/fixtures.json
        experiments/rust_instruction_mix/holdout/gcd.rs
        experiments/rust_instruction_mix/holdout/oracles.py
        experiments/rust_instruction_mix/placement.py
        experiments/rust_instruction_mix/sample_layout.py
        experiments/rust_operand_bytes/collect.py
    """.split())
    for source in HERE.rglob("*"):
        if source.is_file() and source.suffix in {".py", ".S", ".c", ".h", ".txt"}:
            sources.add(str(source.relative_to(ROOT)))
    for source in ("evaluation-plan.json", "calibration.json"):
        sources.add(str((HERE/source).relative_to(ROOT)))
    archives = {case["binary_archive"] for case in cases.values()}
    return {
        "schema": 1, "rounds": 4, "metered_phases": ["fast", "trips"], "benchmark_cpu": 1, "observer_cpus": [0],
        "design": "The two diagnostics run between native-before and native-after. Reverse the case order in even "
                  "rounds and rotate by five positions in rounds three and four. Reverse diagnostic order when "
                  "the canonical case index plus zero-based round is odd. For every child, open the immutable "
                  "registered input afresh as a regular, read-only FD 0. Generate inputs once, hash and warm "
                  "all files before the first sample, and hash again after the last. Use whole-child wait4 CPU.",
        "image_manifest_sha256": prior["image_manifest_sha256"], "tool_sha256": tools,
        "sources_sha256": {key: digest(ROOT/key) for key in sorted(sources)},
        "archives_sha256": {key: digest(ROOT/key) for key in sorted(archives)}, "cases": [cases[k] for k in order],
    }


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("probe", type=Path)
    cli.add_argument("--write", action="store_true", help="write the registration instead of checking it")
    args = cli.parse_args()
    result = register(args.probe)
    path = HERE/"protocol.json"
    if args.write:
        path.write_text(json.dumps(result, indent=2)+"\n")
    elif read(path) != result:
        raise ValueError("registration differs from source fixtures or probe binary")
    print(digest(path))
