#!/usr/bin/env python3
"""Acquire the GCD successor-history study on guest CPU 1 under the registered protocol."""
import runpy
from study import BASE

if __name__ == "__main__":
    # The collector imports this directory's study for its registration and schedule.
    runpy.run_path(str(BASE/"collect.py"), run_name="__main__")
