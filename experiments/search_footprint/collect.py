#!/usr/bin/env python3
"""Run the original pinned collector with this experiment's schedule and schema-2 reader."""

import runpy
from study import BASE

if __name__ == "__main__":
    runpy.run_path(str(BASE / "collect.py"), run_name="__main__")
