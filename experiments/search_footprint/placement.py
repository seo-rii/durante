#!/usr/bin/env python3
"""Replay the scheduler trace, pinned process lifetimes, CPU masks and platform receipts."""

import runpy
import sys
from study import ROOT, rows

if __name__ == "__main__":
    sys.path.insert(0, str(ROOT / "experiments/rust_instruction_mix"))
    replay = runpy.run_path(str(ROOT / "experiments/rust_instruction_mix/placement.py"))
    replay["main"](rows, "/study/elfs")
