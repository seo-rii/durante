"""Replay Scrivener CPU-1 profiles, reconstruct sealed forecasts and evaluate deadlines."""
from math import ceil
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from scrivener_cache.score import fraction_in_range
from scrivener_pages import verify as pages
from scrivener_rank.bench import STUDY


def fixed_branch_range(low, high, observed_bcm):
    """Bound every forecast that gives half weight to Bcm and half to a scan fraction in [0, 1]."""
    fraction = fraction_in_range(low["features"]["bcm"], high["features"]["bcm"], observed_bcm)
    start = low["median_native_ns"]
    span = high["median_native_ns"] - start
    return sorted(ceil(start + span * (fraction + s) / 2) for s in (0, 1))


def verify(root):
    report = pages.verify(root, STUDY, STUDY.scoring.qualification)
    anchors = report["cases"]
    # These post-run bounds describe the model family independently of the chosen scan.
    for name, programs in (report["cases"] | report["controls"]).items():
        if name in ("ascending", "shuffled"):
            continue
        for program, case in programs.items():
            case["fixed_branch_range_ns"] = fixed_branch_range(
                anchors["ascending"][program], anchors["shuffled"][program], case["features"]["bcm"])
    return report


if __name__ == "__main__":
    pages.main(verify)
