#!/usr/bin/env python3
"""Register the history-probe comparison against prior measurements and frozen weights."""
import argparse
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
RESULTS = HERE.parent/"results"
PREVIOUS = HERE.parent/"gcd_holdout/protocol.json"
FILES = (
    "collect.py", "evaluation-plan.json", "fixtures.py", "model.py", "permuted.rs", "placement.py", "plan.json",
    "register.py", "score.py", "study.py", "verify.py",
)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def registration(probe):
    previous = json.loads(PREVIOUS.read_bytes())
    fixture_path = RESULTS/"gce-gcd-history/fixtures.json"
    cases = json.loads(fixture_path.read_bytes())["cases"]
    order = [r["workload_id"] for r in json.loads((HERE/"plan.json").read_bytes())["cases"]]
    if len(set(order)) != 6 or [c["workload_id"] for c in cases] != order:
        raise ValueError("different fixture registration")
    sources = dict(previous["sources_sha256"])
    if any(digest(ROOT/key) != expected for key, expected in sources.items()):
        raise ValueError("previous-study or training source changed")
    probe_files = [p for p in (HERE/"probe").glob("*") if p.is_file() and p.suffix in (".py", ".c", ".h", ".S", ".txt", ".patch")]
    if not {"CMakeLists.txt", "receipt.py"}.issubset({p.name for p in probe_files}):
        raise ValueError("incomplete history probe sources")
    previous_results = RESULTS/"gce-gcd-controls/holdout"
    inputs = [PREVIOUS, fixture_path, *probe_files, *(HERE/name for name in FILES),
              *(previous_results/name for name in ("dataset.json.gz", "receipts.tar.xz", "placement.json.gz",
                                                   "platform.json", "evaluation.json"))]
    sources.update((str(path.relative_to(ROOT)), digest(path)) for path in inputs)
    archives = {c["binary_archive"] for c in cases}
    tools = dict(previous["tool_sha256"], trip_probe=digest(probe))
    if tools["trip_probe"] == previous["tool_sha256"]["trip_probe"]:
        raise ValueError("expected the version-2 history probe")
    return {
        "schema": 1, "rounds": 4, "metered_phases": ["fast", "trips"],
        "benchmark_cpu": 1, "observer_cpus": [0], "image_manifest_sha256": previous["image_manifest_sha256"],
        "tool_sha256": tools, "trip_schema": 2,
        "design": "Each native-bracketed quartet measures version 9001 alone and with one version-2 history "
                  "probe. The latter retains all raw length changes and counts previously unpredicted changes. "
                  "Reverse the case sequence in even rounds and rotate by three cases for rounds three and four. "
                  "Reverse diagnostic order when canonical case index plus zero-based round is odd. Each child "
                  "gets a fresh read-only regular stdin; hash all files before and after the schedule.",
        "previous_protocol_sha256": digest(PREVIOUS),
        "previous_evaluation_sha256": digest(previous_results/"evaluation.json"),
        "sources_sha256": dict(sorted(sources.items())),
        "archives_sha256": {k: digest(ROOT/k) for k in sorted(archives)}, "cases": cases,
    }


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("probe", type=Path)
    cli.add_argument("--write", action="store_true")
    args = cli.parse_args()
    path = HERE/"protocol.json"
    value = registration(args.probe)
    if args.write:
        path.write_text(json.dumps(value, indent=2)+"\n")
    elif json.loads(path.read_bytes()) != value:
        raise ValueError("history registration differs")
    print(digest(path))
