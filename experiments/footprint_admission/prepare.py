"""Package the registered runtime, probes, workloads and collector for the pinned guest."""
import argparse
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from calibrated_limits.prepare import prepare

if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("out", type=Path)
    cli.add_argument("--runtime", type=Path, required=True)
    cli.add_argument("--probe", type=Path, required=True)
    args = cli.parse_args()
    prepare(args.out, args.runtime.resolve(), study=HERE, probe=args.probe.resolve(),
            source_dirs=(HERE.parent / "shape_calibration",))
