"""Bind the cost controls to the registered six-period C++ locality schedule."""
import hashlib
import json
from pathlib import Path
import runpy

ROOT = Path(__file__).resolve().parent
PREVIOUS = ROOT / "base/schedule.py" if (ROOT / "base").is_dir() else ROOT.parent / "cpp20_locality/schedule.py"
previous = runpy.run_path(str(PREVIOUS))
ARMS = ["native", "live", "empty", "gate", "full", "fused"]


def build(cases):
    renamed = dict(zip(previous["ARMS"], ARMS, strict=True), warmup="warmup")
    return [dict(row, arm=renamed[row["arm"]]) for row in previous["build"](cases)]


def main():
    names = ["bench.py", "schedule.py", "guest.sh", "cases.json", "production_reader.py", "v11.so",
             "empty.so", "gate.so", "span.so", "fused.so", "base/bench.py", "base/schedule.py", "design.json"]
    for group in ("bin", "dynamorio"):
        names.extend(p.relative_to(ROOT).as_posix() for p in (ROOT / group).rglob("*") if p.is_file())
    files = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in sorted(names)}
    plan = dict(schema=1, cpu=1, work_budget=100_000_000_000, poll_interval_ms=5, trial_timeout_s=120,
                tools_sha256=files, schedule=build(json.loads((ROOT / "cases.json").read_bytes())))
    (ROOT / "protocol.json").write_text(json.dumps(plan, indent=2) + "\n")


if __name__ == "__main__":
    main()
