#!/usr/bin/env python3
"""Reconcile the dependency-pair children with the recorded scheduler events."""
import sys

from study import HERE, load_protocol, validated_rows

sys.path.insert(0, str(HERE.parent / "rust_instruction_mix"))
from placement import main


def layout(dataset):
    for _, row in validated_rows(dataset, load_protocol(dataset)):
        yield row, None, row["phase_order"]


if __name__ == "__main__":
    main(layout, "/study/elfs")
