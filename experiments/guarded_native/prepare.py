"""Materialize the exact guarded-sampler inputs for the native-only CPU reference."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from cpp20_locality.prepare import materialize
from cpp20_locality.verify import REPO, require, sha
from first_run_sampler.prepare import BASE, control_binary
from guarded_sampler.prepare import mix_binary

GUARDED = HERE.parent / "guarded_sampler"
GUARDED_PROTOCOL_SHA = "acbd8e208427813106120528edb7c9873b884a3f825154755c60efee0117a479"


def registration():
    previous = (GUARDED / "protocol.json").read_bytes()
    require(sha(previous) == GUARDED_PROTOCOL_SHA, "different guarded-sampler registration")
    frozen = json.loads(previous)["tools_sha256"]
    cases_bytes = (GUARDED / "cases.json").read_bytes()
    require(sha(cases_bytes) == frozen["cases.json"], "different case manifest")
    cases = json.loads(cases_bytes)
    ids = sorted((c["case_id"] for c in cases), key=lambda n: hashlib.sha256(n.encode()).digest())
    require(len(set(ids)) == len(ids) == 18, "wrong number of native reference cases")
    # Each program's calibration input is its first execution. Its single warmup
    # can therefore serve as a paid native anchor before any held-out run.
    anchors = {c["case_id"] for c in cases if c["split"] == "calibration"}
    warmups = sorted(ids, key=lambda n: n not in anchors)
    schedule = [dict(case_id=n, rep=0, arm="warmup") for n in warmups]
    for r in range(9):
        # Every input occupies one position in each two-position band across the nine rounds.
        rotation = 2 * r
        schedule.extend(dict(case_id=n, rep=r + 1, arm="native") for n in ids[rotation:] + ids[:rotation])
    files = {"bench.py": frozen["base/bench.py"], "guest.sh": frozen["guest.sh"],
             "production_reader.py": frozen["production_reader.py"], "cases.json": frozen["cases.json"]}
    for c in cases:
        name = "bin/" + c["program"]
        require(frozen[name] == c["binary_sha256"], "inconsistent executable identity")
        files[name] = frozen[name]
    return dict(schema=1, guarded_protocol_sha256=GUARDED_PROTOCOL_SHA, cpu=1, trial_timeout_s=120,
                tools_sha256=files, schedule=schedule)


def sources(reader=REPO / "collect.py"):
    return {"bench.py": BASE / "bench.py", "guest.sh": BASE / "guest.sh", "cases.json": GUARDED / "cases.json",
            "production_reader.py": reader}


def prepare(out, elf_archive, reader=REPO / "collect.py"):
    require(json.loads((HERE / "protocol.json").read_bytes()) == registration(), "native registration differs")
    files = sources(reader)
    with tempfile.TemporaryDirectory(dir=out.parent) as directory:
        for name, raw in (("path_switch", control_binary()), ("path_mix", mix_binary())):
            file = Path(directory) / name
            file.write_bytes(raw)
            files["bin/" + name] = file
        materialize(out, None, elf_archive, HERE, files)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path)
    parser.add_argument("--elf-archive", type=Path, required=True, help="Aonohako #154 corpus/elfs.tar.xz")
    parser.add_argument("--reader", type=Path, default=REPO / "collect.py", help="registered v11 collect.py")
    args = parser.parse_args()
    prepare(args.out, args.elf_archive, args.reader)
