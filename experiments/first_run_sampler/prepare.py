"""Materialize the registered single-calibration experiment from the frozen executables and clients."""
import argparse
from pathlib import Path
import sys
import tarfile
import tempfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from cpp20_locality.prepare import materialize
from cpp20_locality.verify import require, sha

BASE = HERE.parent / "cpp20_locality"
COST = HERE.parent / "live_sampler_cost"
FAST = HERE.parent / "live_sampler_fast"
CONTROL_ARCHIVE = "faef6eb411cb74992216f294ff0d17c2b7403165b0a96126a39d7e2ac5b09a83"


def study_files():
    files = {n: HERE / n for n in ("bench.py", "schedule.py", "cases.json", "design.json", "profile.py")}
    return {**files, "base/bench.py": BASE / "bench.py", "guest.sh": BASE / "guest.sh",
            "cost_bench.py": COST / "bench.py"}


def control_binary():
    path = HERE / "control/elf.tar.xz"
    require(sha(path.read_bytes()) == CONTROL_ARCHIVE, "wrong path-switch archive")
    with tarfile.open(path, "r:xz") as archive:
        entries = archive.getmembers()
        require(len(entries) == 1 and entries[0].isfile() and entries[0].name == "bin/path_switch",
                "wrong path-switch archive member")
        return archive.extractfile(entries[0]).read()


def prepare(out, release, elf_archive, reader, span, profiled):
    with tempfile.TemporaryDirectory(dir=out.parent) as tmp:
        control = Path(tmp) / "path_switch"
        control.write_bytes(control_binary())
        sources = {**study_files(), "v11.so": release / "lib/libdurante.so", "production_reader.py": reader,
                   "span.so": span, "profiled.so": profiled, "bin/path_switch": control}
        materialize(out, release, elf_archive, HERE, sources)


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("out", type=Path, help="new guest payload directory")
    cli.add_argument("--release", type=Path, required=True, help="v11 installation with lib/ and dynamorio/")
    cli.add_argument("--elf-archive", type=Path, required=True, help="Aonohako #154 corpus/elfs.tar.xz")
    cli.add_argument("--reader", type=Path, default=HERE.parents[1] / "collect.py", help="release collect.py")
    cli.add_argument("--span", type=Path, required=True, help="full libaccess_span.so")
    cli.add_argument("--profiled", type=Path, required=True, help="libsampler_profiled.so from Durante #80")
    args = cli.parse_args()
    prepare(args.out, args.release, args.elf_archive, args.reader, args.span, args.profiled)
