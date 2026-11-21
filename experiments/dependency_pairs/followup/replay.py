#!/usr/bin/env python3
"""Replay the second dependency-pair experiment with its own frozen protocol."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory

HERE = Path(__file__).resolve().parent
STUDY = HERE.parent
ROOT = STUDY.parents[1]
OPERATIONS = {"score": "score.py", "receipts": "verify_receipts.py", "placement": "verify_placement.py"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=OPERATIONS)
    parser.add_argument("arguments", nargs=argparse.REMAINDER, help="arguments to the corresponding primary replay tool")
    args = parser.parse_args()
    protocol = HERE / "protocol.json"
    plan = json.loads(protocol.read_bytes())
    # The primary acquisition and scoring tools read a protocol next to their
    # source. Keep their filesystem contract for this second, independent run.
    # All executable inputs come from this repository. Archive paths are passed
    # only to the corresponding verifier as data.
    relative_study = STUDY.relative_to(ROOT)
    paths = {relative_study / "study.py", relative_study / OPERATIONS[args.operation]}
    if args.operation == "score":
        paths.update((Path("experiments/rust_operand_bytes/model_limit.py"), Path(plan["clock"]["evaluation"])))
    elif args.operation == "placement":
        paths.update((Path("experiments/rust_instruction_mix/placement.py"), Path("experiments/rust_instruction_mix/sample_layout.py")))
    else:
        paths.update((Path("collect.py"), Path("experiments/rust_operand_bytes/collect.py"),
                      Path("experiments/rust_instruction_mix/collect.py")))
    with TemporaryDirectory() as tmp:
        tree = Path(tmp)
        for source in paths:
            if source.is_absolute() or ".." in source.parts:
                raise ValueError("expected repository-relative input")
            (tree/source).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT/source, tree/source)
        staged = tree / relative_study
        shutil.copyfile(protocol, staged/"protocol.json")
        result = subprocess.run([sys.executable, staged/OPERATIONS[args.operation], *args.arguments])
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
