"""Register one calibration per ELF, then the six permutations of the three holdout arms."""
import hashlib
import itertools
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ARMS = ("live", "full", "profiled")


def build(cases):
    calibrations = [c for c in cases if c["split"] == "calibration"]
    holdouts = [c for c in cases if c["split"] == "holdout"]
    hashes = [c["binary_sha256"] for c in calibrations]
    if len(set(hashes)) != len(hashes) or set(hashes) != {c["binary_sha256"] for c in holdouts}:
        raise ValueError("every held-out ELF must have exactly one first execution")
    schedule = [dict(case_id=c["case_id"], rep=0, arm="calibration") for c in calibrations]
    orders = list(itertools.permutations(ARMS))
    for rep in range(len(orders)):
        for position in range(len(holdouts)):
            i = (position + rep) % len(holdouts)
            for arm in orders[(i + rep) % len(orders)]:
                schedule.append(dict(case_id=holdouts[i]["case_id"], rep=rep + 1, arm=arm))
    return schedule


def main():
    names = ["bench.py", "schedule.py", "profile.py", "guest.sh", "cases.json", "design.json",
             "production_reader.py", "base/bench.py", "cost_bench.py", "v11.so", "span.so", "profiled.so"]
    for group in ("bin", "dynamorio"):
        names.extend(p.relative_to(ROOT).as_posix() for p in (ROOT / group).rglob("*") if p.is_file())
    files = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in sorted(names)}
    plan = dict(schema=1, cpu=1, work_budget=100_000_000_000, poll_interval_ms=5, trial_timeout_s=120,
                tools_sha256=files, schedule=build(json.loads((ROOT / "cases.json").read_bytes())))
    (ROOT / "protocol.json").write_text(json.dumps(plan, indent=2) + "\n")


if __name__ == "__main__":
    main()
