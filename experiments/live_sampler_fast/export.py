"""Publish exact live sampler candidates' receipts, binary observations and scheduler evidence."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cpp20_locality.export import publish

JSON_ARMS = {"catalog", "gate", "full", "profiled"}

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument("out", type=Path)
    args = parser.parse_args()
    publish(args.capture, args.out, sample_arms=JSON_ARMS, binary_sample_arms={"packed"},
            clock_arms=JSON_ARMS | {"live", "packed"})
