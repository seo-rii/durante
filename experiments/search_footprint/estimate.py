"""Predict CPU time offline for one captured process using the registered Rust search profile."""

import argparse
import hashlib
import json
from pathlib import Path
import runpy

from model import DATA, HERE, ROOT, baseline, estimate

validate = runpy.run_path(str(HERE / "probe/receipt.py"))["validate"]
production = runpy.run_path(str(ROOT / "experiments/divide_clock/fast_reader.py"))[
    "production"
]


def report(elf, records, sample_path, profile):
    sample = json.loads(sample_path.read_bytes())
    pid = sample["pid"]
    validate(sample, pid)
    observation = {"sample": sample, "production": production(records, pid)}
    native, reason = estimate(
        observation, hashlib.sha256(elf.read_bytes()).hexdigest(), profile
    )
    return {
        "pid": pid,
        "status": reason,
        "baseline_ns": round(baseline(observation)),
        "estimated_ns": None if native is None else round(native),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--elf", type=Path, required=True)
    parser.add_argument(
        "--records",
        type=Path,
        required=True,
        help="v9001 process-<pid>.jsonl directory",
    )
    parser.add_argument(
        "--sample",
        type=Path,
        required=True,
        help="schema-2 footprint sample from the same process",
    )
    args = parser.parse_args()
    print(
        json.dumps(
            report(
                args.elf,
                args.records,
                args.sample,
                json.loads((DATA / "parameters.json").read_bytes()),
            )
        )
    )
