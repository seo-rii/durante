#!/usr/bin/env python3
"""Rebuild the prospective registration from the preceding public training set and the new fixtures."""
import argparse
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DATA = ROOT/"experiments/results/gce-gcd-controls"
FIRST = ROOT/"experiments/gcd_controls/protocol.json"
FILES = (
    "classify.py", "collect.py", "cost_model.py", "fibonacci.rs", "fit.py", "fixtures.py", "parameters.json",
    "placement.py", "plan.json", "register.py", "replay_training.py", "score.py", "study.py", "test_classify.py", "verify.py",
)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def registration():
    previous = json.loads(FIRST.read_bytes())
    fixtures = json.loads((DATA/"holdout/fixtures.json").read_bytes())
    order = [r["workload_id"] for r in json.loads((HERE/"plan.json").read_bytes())["cases"]]
    cases = fixtures["cases"]
    if len(set(order)) != 5 or [c["workload_id"] for c in cases] != order:
        raise ValueError("different fixture registration")
    inherited = previous["sources_sha256"]
    if any(digest(ROOT/key) != expected for key, expected in inherited.items()):
        raise ValueError("first-study source changed")
    sources = dict(inherited)
    inputs = [FIRST, DATA/"holdout/fixtures.json", *(HERE/name for name in FILES),
              *(DATA/name for name in ("dataset.json.gz", "receipts.tar.xz", "placement.json.gz",
                                       "platform.json", "evaluation.json"))]
    sources.update((str(path.relative_to(ROOT)), digest(path)) for path in inputs)
    archives = {c["binary_archive"] for c in cases}
    return {
        "schema": 1, "rounds": 4, "metered_phases": ["fast", "trips"],
        "benchmark_cpu": 1, "observer_cpus": [0], "image_manifest_sha256": previous["image_manifest_sha256"],
        "tool_sha256": previous["tool_sha256"],
        "design": "Reuse the first study's native/diagnostic/diagnostic/native measurement and wait4 method. "
                  "Reverse the case sequence in even rounds and rotate by two cases for rounds three and four. "
                  "Reverse diagnostic order when canonical case index plus zero-based round is odd. Open each "
                  "regular stdin afresh for each child. Hash every input before the schedule and after it.",
        "training_protocol_sha256": digest(FIRST), "training_evaluation_sha256": digest(DATA/"evaluation.json"),
        "sources_sha256": dict(sorted(sources.items())),
        "archives_sha256": {k: digest(ROOT/k) for k in sorted(archives)}, "cases": cases,
    }


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--write", action="store_true")
    path = HERE/"protocol.json"
    value = registration()
    if cli.parse_args().write:
        path.write_text(json.dumps(value, indent=2)+"\n")
    elif json.loads(path.read_bytes()) != value:
        raise ValueError("prospective registration differs")
    print(digest(path))
