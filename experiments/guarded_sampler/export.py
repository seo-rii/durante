"""Publish the registered cost trials and any audit reruns actually requested by the guard."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cpp20_locality.export import publish
from guarded_sampler.schedule import effective_schedule

GUARDED = {"guarded", "audit_guarded"}
SAMPLED = GUARDED | {"confirmation", "calibration", "full", "profiled", "audit_fallback"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument("out", type=Path)
    args = parser.parse_args()
    publish(args.capture, args.out, sample_arms=SAMPLED, clock_arms=SAMPLED | {"live"},
            profile_arms={"calibration"}, guard_arms=GUARDED, recovery_schedule=effective_schedule)
