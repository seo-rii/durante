"""Apply the prospective rule back to the first study, including its seven assembly controls."""

import argparse
from collections import defaultdict
from fractions import Fraction
import gzip
import json
from pathlib import Path
import runpy
from statistics import median

from cost_model import estimate
from fit import FIT, ROOT


def replay():
    parent = ROOT / "experiments/gcd_controls"
    data = ROOT / "experiments/results/gce-gcd-controls"
    first = json.loads(gzip.decompress((data / "dataset.json.gz").read_bytes()))
    fit = json.loads(FIT.read_bytes())
    samples = defaultdict(list)
    for row, case, _ in runpy.run_path(str(parent / "study.py"))["rows"](first):
        base, adjusted, observed = estimate(row["trips"], case, fit)
        samples[case["workload_id"]].append((base, adjusted, observed))
    native = json.loads((data / "evaluation.json").read_bytes())["cases"]
    out = {}
    for key, trials in samples.items():
        target = Fraction(native[key]["native_ns"]["median"])
        base, adjusted, observed = zip(*trials)
        out[key] = {
            "native_median_ns": str(target),
            "paired_baseline_ns": [round(v) for v in base],
            "corrected_ns": [round(v) for v in adjusted],
            "corrected_median_ns": round(median(adjusted)),
            "corrected_pass": all(
                abs(v / target - 1) <= Fraction(1, 5) for v in adjusted
            ),
            "observed": list(observed),
        }
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    output = ROOT / "experiments/results/gce-gcd-controls/holdout/training-replay.json"
    result = replay()
    if args.verify:
        if result != json.loads(output.read_bytes()):
            raise ValueError("training replay differs")
    else:
        output.write_text(json.dumps(result, indent=2) + "\n")
    for key, row in result.items():
        print(key, row["corrected_median_ns"], row["corrected_pass"])
