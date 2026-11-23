"""Prepare the pinned guest with both registered samplers and the three specific workload ELFs."""
import argparse
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from calibrated_limits.prepare import prepare

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path)
    parser.add_argument("--runtime", type=Path, required=True, help="registered #82 payload")
    parser.add_argument("--probe", type=Path, required=True, help="registered temporal probe")
    parser.add_argument("--combined-benchmark", action="store_true", help="prepare the paired one-pass study")
    args = parser.parse_args()
    prepare(args.out, args.runtime.resolve(), study=HERE, probe=args.probe.resolve(),
            benchmark="bench_combined.py" if args.combined_benchmark else "bench.py")
