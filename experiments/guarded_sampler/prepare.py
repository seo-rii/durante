"""Materialize frozen programs, the six previous first-run profiles and the guarded client."""
import argparse
import gzip
import json
from pathlib import Path
import sys
import tarfile
import tempfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from cpp20_locality.prepare import materialize
from cpp20_locality.verify import require, sha
from first_run_sampler.prepare import BASE, COST, control_binary
from first_run_sampler.profile import resolve_for_digest

FIRST = HERE.parent / "first_run_sampler"
RESULT = HERE.parent / "results/gce-first-run-sampler"
PREVIOUS_DATA = "d9fcaa4dcada780902d6ce0c04a88873d5cdc75cfccc0cc09ca6e40e6d652dee"
PREVIOUS_RECEIPTS = "ed4ebb3a543b64e6f88b6d61e4ca3307aa7aa77717e383bf0fbe366b1022642e"
MIX_ARCHIVE = "bb1a63a6504c6834f72970f85a9f44addd79a014d4135d41dcf005857bd6adeb"


def profiles_from_results():
    archive_path = RESULT / "receipts.tar.xz"
    data = (RESULT / "dataset.json.gz").read_bytes()
    require(sha(data) == PREVIOUS_DATA and sha(archive_path.read_bytes()) == PREVIOUS_RECEIPTS,
            "different first-run evidence")
    files = {}
    with tarfile.open(archive_path, "r:xz") as archive:
        for row in json.loads(gzip.decompress(data))["rows"]:
            if row["arm"] == "calibration":
                name = row["profile_path"]
                raw = archive.extractfile(name).read()
                elf = row["elf_sha256"]
                require(sha(raw) == row["profile_sha256"] == row["receipts"][name] and elf not in files,
                        "different first-run profile")
                resolve_for_digest(json.loads(raw), elf)
                files[elf] = raw
    require(len(files) == 6, "wrong previous ELF set")
    return files


def mix_binary():
    path = HERE / "control/elf.tar.xz"
    require(sha(path.read_bytes()) == MIX_ARCHIVE, "different mixed-path archive")
    with tarfile.open(path, "r:xz") as archive:
        entries = archive.getmembers()
        require(len(entries) == 1 and entries[0].isfile() and entries[0].name == "bin/path_mix", "wrong control")
        return archive.extractfile(entries[0]).read()


def study_files():
    files = {n: HERE / n for n in ("bench.py", "schedule.py", "guard.py", "cases.json", "design.json")}
    return {**files, "base/bench.py": BASE / "bench.py", "guest.sh": BASE / "guest.sh",
            "cost_bench.py": COST / "bench.py", "first_bench.py": FIRST / "bench.py", "profile.py": FIRST / "profile.py"}


def prepare(out, release, elf_archive, reader, span, profiled, guarded):
    with tempfile.TemporaryDirectory(dir=out.parent) as tmp:
        root = Path(tmp)
        inputs = {"bin/path_switch": control_binary(), "bin/path_mix": mix_binary()}
        inputs.update({"profiles/" + elf + ".json": raw for elf, raw in profiles_from_results().items()})
        sources = {**study_files(), "v11.so": release / "lib/libdurante.so", "production_reader.py": reader,
                   "span.so": span, "profiled.so": profiled, "guarded.so": guarded}
        for name, raw in inputs.items():
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            sources[name] = target
        materialize(out, release, elf_archive, HERE, sources)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path)
    for name in ("release", "elf-archive", "span", "profiled", "guarded"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--reader", type=Path, default=HERE.parents[1] / "collect.py")
    args = parser.parse_args()
    prepare(args.out, args.release, args.elf_archive, args.reader, args.span, args.profiled, args.guarded)
