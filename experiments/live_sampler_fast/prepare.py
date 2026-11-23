"""Materialize the registered six-arm payload, including its static instruction profiles."""
import argparse
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from cpp20_locality.prepare import materialize

BASE = HERE.parent / "cpp20_locality"
COST = HERE.parent / "live_sampler_cost"


def study_files():
    sources = {n: HERE / n for n in ("bench.py", "schedule.py", "cases.json", "design.json", "profiles.json")}
    return {**sources, "base/bench.py": BASE / "bench.py", "base/schedule.py": BASE / "schedule.py",
            "cost_bench.py": COST / "bench.py", "guest.sh": BASE / "guest.sh"}


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("out", type=Path, help="new directory for the guest")
    cli.add_argument("--release", type=Path, required=True, help="Aonohako v11 installation")
    cli.add_argument("--elf-archive", type=Path, required=True, help="Aonohako #154 elfs.tar.xz")
    cli.add_argument("--reader", type=Path, default=HERE.parents[1] / "collect.py", help="release collect.py")
    for name in ("span", "gate", "catalog", "packed", "profiled"):
        cli.add_argument("--" + name, type=Path, required=True)
    args = cli.parse_args()
    clients = {name + ".so": getattr(args, name) for name in ("span", "gate", "catalog", "packed", "profiled")}
    sources = {**study_files(), **clients, "production_reader.py": args.reader, "v11.so": args.release / "lib/libdurante.so"}
    materialize(args.out, args.release, args.elf_archive, HERE, sources)
