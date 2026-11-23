"""Rebuild the pinned live sampler cost payload from the previous release and new clients."""
import argparse
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from cpp20_locality.prepare import materialize

BASE = HERE.parent / "cpp20_locality"


def study_files():
    return {"bench.py": HERE / "bench.py", "schedule.py": HERE / "schedule.py", "design.json": HERE / "design.json",
            "guest.sh": BASE / "guest.sh", "cases.json": BASE / "cases.json",
            "base/bench.py": BASE / "bench.py", "base/schedule.py": BASE / "schedule.py"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path, help="new directory for the guest")
    parser.add_argument("--release", type=Path, required=True, help="Aonohako v11 installation")
    parser.add_argument("--elf-archive", type=Path, required=True, help="Aonohako #154 elfs.tar.xz")
    parser.add_argument("--reader", type=Path, default=HERE.parents[1] / "collect.py", help="release collect.py")
    for name in ("span", "empty", "gate", "fused"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    sources = {**study_files(), "production_reader.py": args.reader,
               "v11.so": args.release / "lib/libdurante.so", "span.so": args.span,
               "empty.so": args.empty, "gate.so": args.gate, "fused.so": args.fused}
    materialize(args.out, args.release, args.elf_archive, HERE, sources)
