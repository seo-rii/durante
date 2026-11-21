"""Freeze the exact search experiment, toolchain, fixtures and data used for scoring."""
import argparse
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DATA = ROOT / "experiments/results/gce-search-order"
PREVIOUS = ROOT / "experiments/gcd_controls/protocol.json"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def registration(probe):
    previous = json.loads(PREVIOUS.read_bytes())
    cases = json.loads((DATA / "fixtures.json").read_bytes())["cases"]
    if len(cases) != 10 or len({c["workload_id"] for c in cases}) != 10:
        raise ValueError("expected ten distinct workloads")
    sources = {
        ROOT / name for name in previous["sources_sha256"]
        if name.startswith("client/") or name in {
            "collect.py", "experiments/rust_operand_bytes/collect.py",
            "experiments/divide_clock/fast_reader.py", "experiments/rust_instruction_mix/collect.py",
            "experiments/rust_instruction_mix/placement.py", "experiments/rust_instruction_mix/sample_layout.py",
            "experiments/rust_instruction_mix/holdout/lower_bound.rs", "experiments/rust_instruction_mix/holdout/fixtures.json",
            "experiments/gcd_controls/study.py", "experiments/gcd_controls/trip_probe/receipt.py",
            "experiments/divide_clock/fast/fast.c", "experiments/divide_clock/fast/CMakeLists.txt",
        }
    }
    sources.update((PREVIOUS, ROOT / "experiments/gcd_controls/calibration.json", DATA / "fixtures.json", ROOT / "experiments/results/gce-rust-operand-bytes/evaluation.json",
                    ROOT / "experiments/gcd_controls/study.py", ROOT / "experiments/gcd_controls/trip_probe/receipt.py"))
    for source in HERE.rglob("*"):
        if source.name != "protocol.json" and source.suffix in {".py", ".c", ".h", ".S", ".txt", ".json", ".rs"}:
            sources.add(source)
    tools = {k: previous["tool_sha256"][k] for k in ("fast", "launcher", "runtime", "production_reader")}
    tools["search_probe"] = digest(probe)
    archives = {case["binary_archive"] for case in cases}
    return {"schema": 1, "rounds": 4, "metered_phases": ["fast", "sampled"], "benchmark_cpu": 1, "observer_cpus": [0],
            "image_manifest_sha256": previous["image_manifest_sha256"], "tool_sha256": tools,
            "design": "Run the fast divider meter with and without the terminal-load sample between two native processes. "
                      "Reverse workload order on even rounds and rotate five places for rounds three and four. Alternate "
                      "the two meter positions for each case. Every child reopens its registered read-only input. Hash "
                      "and warm all regular inputs before acquisition and hash them again after the final case.",
            "sources_sha256": {str(path.relative_to(ROOT)): digest(path) for path in sorted(sources)},
            "archives_sha256": {k: digest(ROOT / k) for k in sorted(archives)}, "cases": cases}


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("probe", type=Path)
    cli.add_argument("--write", action="store_true")
    args = cli.parse_args()
    value = registration(args.probe)
    output = HERE / "protocol.json"
    if args.write:
        output.write_text(json.dumps(value, indent=2) + "\n")
    elif json.loads(output.read_bytes()) != value:
        raise ValueError("search registration differs from its inputs")
    print(digest(output))
