#!/usr/bin/env python3
"""Replay every registered ELF, child receipt, output, clock stream and trip diagnostic."""
import argparse
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import runpy

from study import BASE, plan


def verify(dataset, archive, raw_dir=None, inputs=None):
    # Run the same byte-for-byte receipt verifier with the history study module.
    replay = runpy.run_path(str(BASE/"verify.py"))
    with redirect_stdout(StringIO()):
        replay["verify"](dataset, archive, raw_dir, inputs)
    registration = plan()
    assays = len(registration["cases"]) * registration["rounds"]
    print(f"Verified {len(registration['cases'])} ELFs, {assays} quartets, {4*assays} wait4 receipts/outputs, "
          f"{2*assays} canonical streams and {assays} trip reports.")


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("dataset", type=Path)
    cli.add_argument("archive", type=Path)
    cli.add_argument("--pack", type=Path, help="original guest receipt directory")
    cli.add_argument("--inputs", type=Path, help="locally materialized stdin directory")
    args = cli.parse_args()
    verify(args.dataset, args.archive, args.pack, args.inputs)
