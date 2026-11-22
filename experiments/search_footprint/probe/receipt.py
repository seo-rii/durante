"""Validate the schema-2 sampled element-index range and original pair schedule."""

from pathlib import Path
import runpy

ORIGINAL = Path(__file__).resolve().parents[2] / "search_order/probe/receipt.py"
previous = runpy.run_path(str(ORIGINAL))
PERIOD = previous["PERIOD"]


def validate(data, pid):
    if (
        type(data) is not dict
        or type(data.get("schema")) is not int
        or data["schema"] != 2
        or not {"sampled_min_index", "sampled_max_index"} <= data.keys()
    ):
        raise ValueError("invalid footprint receipt fields")
    original = {
        k: v
        for k, v in data.items()
        if k not in ("sampled_min_index", "sampled_max_index")
    }
    previous["validate"]({**original, "schema": 1}, pid)
    lo, hi = data["sampled_min_index"], data["sampled_max_index"]
    samples = 2 * data["pairs"] + (data["visits"] % PERIOD == 1)
    if not samples:
        if lo is not None or hi is not None:
            raise ValueError("empty footprint must have null index bounds")
        return data
    if type(lo) is not int or type(hi) is not int or not 0 <= lo <= hi < 2**64:
        raise ValueError("invalid sampled element-index range")
    if samples == 1 and lo != hi:
        raise ValueError("one observed index must have equal bounds")
    width = ((hi >> 4) ^ (lo >> 4)).bit_length()
    histogram = data["suffix_histogram"]
    if any(histogram[width + 1 :]) or (samples == 2 and histogram[width] != 1):
        raise ValueError("sampled transition widths disagree with the footprint")
    return data
