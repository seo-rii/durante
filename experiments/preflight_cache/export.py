"""Archive the pinned session study, including every measured executable seen in the guest trace."""
import argparse
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from calibrated_limits.export import publish
from calibrated_limits.verify import METER
from cpp20_locality.export import parse_trace
from preflight_cache import session


def measured_pids(summary, files):
    events, _ = parse_trace(files["output/exec-trace"].decode())
    names = set(METER) | set(summary["seeds"])
    return {pid for pid, rows in events.items() if any(item["executable"] in names for item in rows)}


def export(job, out):
    extra = ("preflight_cache/plan.json", "preflight_cache/bench.py", "shape_calibration/bench.py",
             "shape_calibration/window.cpp", "calibrated_limits/lookup.cpp", "calibrated_limits/fenwick.cpp",
             "guarded_sampler/protocol.json", "guarded_native/archived_reference.json")
    sources = ["experiments/" + name for name in (*session.SOURCE_FILES, *extra)]
    publish(job, out, select_pids=measured_pids, sources=[*sources, "collect.py"],
            plan_name="experiments/preflight_cache/plan.json")
    # Store the guest's versioned list as well as its hashes. Future replays
    # need not load or hash the current session implementation.
    manifest_path = out / "collection.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["cache_implementation_files"] = list(session.SOURCE_FILES)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("job", type=Path)
    cli.add_argument("out", type=Path)
    args = cli.parse_args()
    export(args.job, args.out)
