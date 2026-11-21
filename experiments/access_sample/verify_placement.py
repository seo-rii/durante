#!/usr/bin/env python3
"""Check the 144 measured children against observed guest CPU and executable paths."""
import sys
from study import HERE, load_plan, validated_rows

sys.path.insert(0, str(HERE.parent / "rust_instruction_mix"))
from placement import main


def layout(dataset):
    for _, row in validated_rows(dataset, load_plan(dataset)):
        yield row, None, row["phase_order"]


if __name__ == "__main__":
    main(layout, "/study/elfs")
