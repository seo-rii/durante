"""Publish the actual guest receipts, keeping exec trace lines for the measured PIDs."""
import argparse
import io
import json
from pathlib import Path
import sys
import tarfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from cpp20_locality.export import parse_trace
from cpp20_locality.verify import require, sha


def limit_pids(summary, files):
    pids = {r["pid"] for r in summary["native"]}
    for case in summary["calibrations"]:
        policy = json.loads(files[f"output/policies/{case['program']}.json"])
        pids.update((policy["anchor"]["pid"], policy["native"]["pid"]))
    for run in summary["runs"]:
        pids.add(json.loads(files[f"output/live/{run['sequence']:03d}/result.json"])["pid"])
    return pids


SOURCES = ("experiments/calibrated_limits/bench.py", "experiments/calibrated_limits/plan.json",
           "experiments/guarded_native/live.py", "experiments/guarded_native/anchor.py", "collect.py")


def publish(job, out, *, select_pids=limit_pids, sources=SOURCES, plan_name="experiments/calibrated_limits/plan.json"):
    cleanup = json.loads((job / "cleanup.json").read_bytes())
    require((job / "ops.exit").read_text().strip() == "0" and cleanup["instance_http"] == cleanup["boot_disk_http"] == 404
            and cleanup["key_removed"] is True, "incomplete cleanup")
    platform = json.loads((job / "platform.json").read_bytes())
    require(platform["cpu_platform"] == "AMD Milan" and platform["machine_type"] == "n2d-standard-4"
            and platform["threads_per_core"] == 1, "unexpected guest")
    with tarfile.open(job / "raw.tar.gz", "r:gz") as raw:
        files = {m.name: raw.extractfile(m).read() for m in raw if m.isfile() and m.name.startswith("output/")}
    summary = json.loads(files["output/summary.json"])
    pids = select_pids(summary, files)
    _, lines = parse_trace(files["output/exec-trace"].decode())
    files["output/exec-trace"] = "".join(l + "\n" for pid, l in lines if pid in pids).encode()
    out.mkdir(parents=True, exist_ok=True)
    with tarfile.open(out / "output.tar.xz", "w:xz") as tar:
        for name, raw in sorted(files.items()):
            item = tarfile.TarInfo(name)
            item.size = len(raw)
            item.mode = 0o600
            tar.addfile(item, io.BytesIO(raw))
    guest = {name: sha((job / "payload/repo" / name).read_bytes()) for name in sources}
    require(guest[plan_name] == summary["plan_sha256"], "guest registration differs")
    receipt = dict(platform=platform, cleanup=cleanup, input_sources_sha256=guest,
                   output_sha256=sha((out / "output.tar.xz").read_bytes()))
    (out / "collection.json").write_text(json.dumps(receipt, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job", type=Path)
    parser.add_argument("out", type=Path)
    args = parser.parse_args()
    publish(args.job, args.out)
