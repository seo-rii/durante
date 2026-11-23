"""Publish the receipt archive and exact collector inputs for the registered shape experiment."""
import argparse
import json
import lzma
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from calibrated_limits.export import publish
from cpp20_locality.verify import require, sha


def pids(summary, files):
    owners = {r["pid"] for r in summary["native"]}
    for row in summary["rows"]:
        folder = "output/cases/" + row["case_id"]
        owners.update((row["profile_pid"], row["temporal_pid"], row["plain_pid"]))
        if folder + "/policy.json" in files:
            owners.add(json.loads(files[folder + "/policy.json"])["native"]["pid"])
        for run in row["live"]:
            r = json.loads(files[f"{folder}/live/{run['index']:02d}/result.json"])
            owners.add(r["pid"])
    return owners


def export(job, out):
    study = "experiments/shape_calibration/"
    sources = [study + name for name in ("plan.json", "rules.json", "shape.py", "temporal.py", "select_policy.py",
                                         "capture.py", "bench.py", "window.cpp", "probe/temporal.patch",
                                         "probe/CMakeLists.txt")]
    sources.extend("experiments/calibrated_limits/" + name for name in ("fenwick.cpp", "lookup.cpp"))
    sources.extend(["experiments/guarded_native/anchor.py", "experiments/guarded_native/live.py", "collect.py"])
    publish(job, out, select_pids=pids, sources=sources, plan_name=study + "plan.json")
    probe = (job / "payload/temporal.so").read_bytes()
    expected = json.loads((job / "payload/repo" / study / "rules.json").read_bytes())["probe_sha256"]
    require(sha(probe) == expected, "different registered sidecar")
    (out / "temporal.so.xz").write_bytes(lzma.compress(probe))


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("job", type=Path)
    cli.add_argument("out", type=Path)
    args = cli.parse_args()
    export(args.job, args.out)
