#!/usr/bin/env python3
"""Reconcile the scheduler's recorded workload PIDs with the operand-byte experiment."""
import json

from evaluate import HERE, children, require, sha
from placement import main


def layout(dataset):
    raw = (HERE / "protocol.json").read_bytes()
    require(dataset["protocol_sha256"] == sha(raw), "wrong frozen protocol")
    for _, row in children(dataset, json.loads(raw)):
        yield row, None, row["phase_order"]


if __name__ == "__main__":
    main(layout, "/study/elfs")
