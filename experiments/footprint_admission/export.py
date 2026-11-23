"""Publish the registered guest evidence and retain exec events for every measured process."""
import argparse
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from calibrated_limits.export import publish


def pids(summary, files):
    owners = {row["pid"] for row in summary["native"]}
    for row in summary["rows"]:
        owners.add(row["profile_pid"])
        folder = "output/cases/" + row["case_id"]
        if folder + "/policy.json" in files:
            owners.add(json.loads(files[folder + "/policy.json"])["native"]["pid"])
        for event in row["limits"]:
            path = f"{folder}/limits/{event['method']}/{event['index']:02d}/live/result.json"
            owners.add(json.loads(files[path])["pid"])
    return owners


def export(job, out):
    dirs = {
        "footprint_admission": ("plan.json", "rules.json", "admission.py", "bench.py"),
        "shape_calibration": ("run.py", "select_policy.py", "shape.py", "temporal.py",
                              "combined.py", "bench.py", "window.cpp"),
        "calibrated_limits": ("fenwick.cpp", "lookup.cpp"),
        "guarded_native": ("anchor.py", "live.py"),
    }
    sources = [f"experiments/{name}/{file}" for name, files in dirs.items() for file in files]
    publish(job, out, select_pids=pids, sources=[*sources, "collect.py"],
            plan_name="experiments/footprint_admission/plan.json")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job", type=Path)
    parser.add_argument("out", type=Path)
    args = parser.parse_args()
    export(args.job, args.out)
