"""Publish captured on-guest first-run profiles, terminal v11 clocks and scheduler evidence."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cpp20_locality.export import publish

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument("out", type=Path)
    args = parser.parse_args()
    publish(args.capture, args.out, sample_arms={"calibration", "full", "profiled"},
            clock_arms={"calibration", "live", "full", "profiled"}, profile_arms={"calibration"})
