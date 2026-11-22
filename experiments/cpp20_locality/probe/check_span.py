#!/usr/bin/env python3
"""Check the two-visit sampling phase, pending endpoints, and the span sidecar."""
import argparse
import json
from pathlib import Path
import sys
import tempfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "access_sample"))
from check_control import build, marked, native, offsets


def check(args, folder, visits, near, far, span):
    binary = build(folder, HERE / "span_control.S", f"-DVISITS={visits}", pie=True)
    native(binary)
    pid, status, stdout, stderr, path = marked(args, folder / f"visits-{visits}", binary)
    if status or stdout or stderr:
        raise ValueError(f"{visits} visits: exit {status}, stdout {stdout!r}, stderr {stderr!r}")
    counts = {"loads": visits, "pairs": near + far, "near_pairs": near, "far_pairs": far}
    sites = [{"offset": offsets(binary)["walk_site"], **counts, "sampled_address_span_bytes": span}] if visits else []
    expected = {"schema": 2, "pid": pid, "threads": 1, "period": 1024, "line_bytes": 64,
                **counts, "sites": sites}
    receipt = json.loads(path.read_bytes())
    if receipt != expected:
        raise ValueError(f"{visits} visits: got {receipt}, expected {expected}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ("drrun", "meter", "probe"):
        parser.add_argument("--" + arg, type=Path, required=True)
    args = parser.parse_args()
    # The 1025th visit expands the span even though its pair is incomplete.
    cases = ((0, 0, 0, None), (1, 0, 0, 0), (2, 1, 0, 64), (1024, 1, 0, 64),
             (1025, 1, 0, 128), (1026, 1, 1, 192))
    with tempfile.TemporaryDirectory() as temp:
        for case in cases:
            check(args, Path(temp), *case)
    print(f"{len(cases)} span controls passed")
