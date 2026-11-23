"""Construct the pinned six-period Williams design and bind the collector payload."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ARMS = ["native", "diag", "diag_near", "diag_span", "live", "live_span"]
FIRST = [0, 1, 5, 2, 4, 3]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(cases):
    schedule = [dict(case_id=c["case_id"], rep=0, arm="warmup") for c in cases]
    for rep in range(6):
        for position in range(len(cases)):
            # Rotate case order and enforce all six instrument positions per case.
            i = (position + rep) % len(cases)
            for k in FIRST:
                arm = ARMS[(k + i + rep) % len(ARMS)]
                schedule.append(dict(case_id=cases[i]["case_id"], rep=rep + 1, arm=arm))
    return schedule


def main():
    cases = json.loads((ROOT / "cases.json").read_bytes())
    files = [ROOT / name for name in ("bench.py", "schedule.py", "guest.sh", "cases.json", "production_reader.py",
                                       "v11.so", "near.so", "span.so")]
    for subdir in ("bin", "dynamorio"):
        files.extend(p for p in (ROOT / subdir).rglob("*") if p.is_file())
    protocol = dict(schema=1, cpu=1, work_budget=100_000_000_000, poll_interval_ms=5, trial_timeout_s=120,
                    tools_sha256={p.relative_to(ROOT).as_posix(): sha(p) for p in sorted(files)},
                    schedule=build(cases))
    (ROOT / "protocol.json").write_text(json.dumps(protocol, indent=2) + "\n")


if __name__ == "__main__":
    main()
