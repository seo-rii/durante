"""Archive every measured native and metered PID, the study registration and the guest sources."""
import argparse
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from calibrated_limits.export import publish
from calibrated_limits.verify import METER
from cpp20_locality.export import parse_trace
from preflight_cache.session import SOURCE_FILES


def export(job, out, mode="primary"):
    base = "experiments/deadline_accuracy/"
    plan_name = "exact-plan.json" if mode == "exact" else "plan.json"
    plan = json.loads((job / "payload/repo" / base / plan_name).read_bytes())
    programs = {case["program"] for case in plan["cases"]}
    measured = set(METER) | programs

    def pids(summary, files):
        events, _ = parse_trace(files["output/exec-trace"].decode())
        return {pid for pid, rows in events.items() if any(r["executable"] in measured for r in rows)}

    sources = {"experiments/" + name for name in SOURCE_FILES}
    files = {
        "primary": (plan_name, "rules.json", "bench.py", "gate.py"),
        "exact": (plan_name, "exact-rules.json", "bench_exact.py", "bench.py", "gate.py"),
        "parity": (plan_name, "client-parity.json", "client_parity.py"),
    }[mode]
    sources.update(base + name for name in files)
    for name in programs:
        path = base + name + ".cpp"
        if not (job / "payload/repo" / path).is_file():
            path = "experiments/calibrated_limits/" + name + ".cpp"
        sources.add(path)
    sources.update(("collect.py", "experiments/shape_calibration/bench.py",
                    "experiments/guarded_sampler/protocol.json", "experiments/guarded_native/archived_reference.json"))
    publish(job, out, select_pids=pids, sources=sorted(sources), plan_name=base + plan_name)


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("job", type=Path)
    cli.add_argument("out", type=Path)
    cli.add_argument("--mode", choices=("primary", "exact", "parity"), default="primary")
    args = cli.parse_args()
    export(args.job, args.out, args.mode)
