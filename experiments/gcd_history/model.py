"""Apply the frozen Euclidean timing weights to a schema-2 loop history measurement."""

from fractions import Fraction
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
PREVIOUS = HERE.parent / "gcd_holdout"
sys.path.append(str(PREVIOUS))
from cost_model import baseline, estimate

FIT = json.loads((PREVIOUS / "parameters.json").read_bytes())


def predict(observation, case):
    receipt = observation["trip"]
    if receipt["schema"] != 2:
        raise ValueError("expected a version-2 loop history measurement")
    base, flat, counts = estimate(observation, case, FIT)
    modes = {s["branch_offset"]: s["mode"] for s in counts["sites"]}
    unpredicted = 0
    for site in receipt["sites"]:
        if modes[site["branch_offset"]] == "remainder":
            changes = site["unpredicted_changes"]
            if type(changes) is not int or not 0 <= changes <= site["changes"]:
                raise ValueError("invalid number of unpredicted length changes")
            unpredicted += changes
    discounted = counts["remainder_length_changes"] - unpredicted
    corrected = flat - Fraction(FIT["ns_per_remainder_length_change"]) * discounted
    return {"paired_baseline": base, "flat_changes": flat, "history": corrected}, {
        **counts,
        "unpredicted_changes": unpredicted,
    }
