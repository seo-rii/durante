#!/usr/bin/env python3
"""Check per-PC pair classifications against the archived two-field successor table."""
import argparse
import gzip
import json
from pathlib import Path
import runpy

HERE = Path(__file__).resolve().parent
locality = runpy.run_path(str(HERE.parent / "dependency_pairs/followup/locality.py"))
ENTRIES = locality["ENTRIES"]
OPERATIONS = locality["OPERATIONS"]
position = locality["position"]


def expected(order, lanes):
    visits = OPERATIONS // 4
    sites = []
    for site in range(4):
        start = site if lanes == 1 else site * visits
        stride = 4 if lanes == 1 else 1
        near = far = 0
        for i in range(0, visits-1, 1024):
            rank = start + i * stride
            # Both 4-byte fields of each 8-byte record share its aligned line.
            a = position(order == "scattered", rank) // 8
            b = position(order == "scattered", rank + stride) // 8
            if abs(a-b) <= 1:
                near += 1
            else:
                far += 1
        sites.append({"loads": visits, "pairs": near+far, "near_pairs": near, "far_pairs": far})
    return sites


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    args = parser.parse_args()
    raw = args.dataset.read_bytes()
    data = json.loads(gzip.decompress(raw) if raw.startswith(b"\x1f\x8b") else raw)
    controls = {f"load_{order}_{name}": expected(order, lanes)
                for order in ("adjacent", "scattered") for name, lanes in (("serial", 1), ("parallel", 4))}
    checked = {name: 0 for name in controls}
    for row in data["samples"]:
        name = row["workload_id"]
        if name not in controls:
            continue
        measured = [{k: v for k, v in s.items() if k != "offset"} for s in row["sampled"]["sample"]["sites"]]
        if measured != controls[name]:
            raise ValueError(f"observations disagree with the successor sequence for {name}")
        checked[name] += 1
    if any(n != 3 for n in checked.values()):
        raise ValueError("missing measured controls")
    print("All 48 per-site records match the expected consecutive address pairs.")


if __name__ == "__main__":
    main()
