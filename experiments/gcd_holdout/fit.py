"""Fit two loop costs on the four matched assembly controls only."""

import argparse
from fractions import Fraction
import hashlib
import json
from pathlib import Path
from statistics import mean

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
FIT = HERE / "parameters.json"
TRAINING = ROOT / "experiments/results/gce-gcd-controls/evaluation.json"
CAL = ROOT / "experiments/gcd_controls/calibration.json"


def fit():
    evaluation = json.loads(TRAINING.read_bytes())
    population = json.loads(CAL.read_bytes())
    cases = evaluation["cases"]
    counted = [cases["loop_counted_" + order] for order in ("original", "grouped")]
    # The two countdown controls retain the hardware recurrence with the exit independent of DIV.
    residual = mean(
        Fraction(c["native_ns"]["median"]) - c["models"]["online_baseline"]["median_ns"]
        for c in counted
    )
    visits = population["population"]["divisions"]
    length_changes = (
        population["population"]["original_changes"]
        - population["population"]["grouped_changes"]
    )
    interaction = Fraction(
        evaluation["order_by_control_interaction"][
            "median_difference_of_differences_ns"
        ]
    )
    return {
        "schema": 1,
        "training_sha256": hashlib.sha256(TRAINING.read_bytes()).hexdigest(),
        "first_study_calibration_sha256": hashlib.sha256(CAL.read_bytes()).hexdigest(),
        "training_controls": [
            "loop_counted_original",
            "loop_counted_grouped",
            "loop_remainder_original",
            "loop_remainder_grouped",
        ],
        "baseline_coefficients_ns": population["baseline_coefficients_ns"],
        "ns_per_eligible_visit": str(residual / visits),
        "ns_per_remainder_length_change": str(interaction / length_changes),
        "counted_mean_residual_ns": str(residual),
        "observed_interaction_ns": str(interaction),
        "training_divisions": visits,
        "training_change_difference": length_changes,
        "scoring": "Report every case and repetition. Use only single-process complete receipts. "
        "For each phase compare the four estimates to the median of the eight native CPU observations. "
        "To pass, every estimate must be within 20 percent. Report the two native quality gates "
        "(minimum 20 ms and spread at most 10 percent), the two distinct paired baseline estimates, "
        "the number of eligible visits and qualifying length changes, the probe CPU overhead, and "
        "the alternating minus runs contrast. Report failures without changing these parameters.",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    weights = fit()
    if args.write:
        with FIT.open("x") as output:
            output.write(json.dumps(weights, indent=2) + "\n")
    elif json.loads(FIT.read_bytes()) != weights:
        raise ValueError("the frozen parameters differ from the four training controls")
    print(
        FIT.name,
        weights["ns_per_eligible_visit"],
        weights["ns_per_remainder_length_change"],
    )
