"""Build the contest subset and freeze the old study's per-problem weights."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import statistics
import subprocess
import sys

HERE = Path(__file__).resolve().parent
STUDY = HERE.parent
ROOT = STUDY.parents[1]
sys.path.insert(0, str(STUDY))
import analyze_v2
import collect as capacity


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("runtime", "client", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir()
    protocol = json.loads((HERE / "protocol.json").read_bytes())
    plan = json.loads((STUDY / "plan-v2.json").read_bytes())
    archive = json.loads((STUDY / "results/v2/results.json").read_bytes())
    names = [n for group in protocol["groups"].values() for n in group]
    plan["cases"] = {n: plan["cases"][n] for n in names}
    plan["sources"] = {n: plan["sources"][n] for n in names}
    binaries = capacity.build(plan, out)
    cases = capacity.prepare_inputs(plan, out, binaries)
    frozen = {}
    for name, case in cases.items():
        case["stdin"] = "inputs/" + Path(case["stdin"]).name
        oracle = hashlib.sha256(case.pop("expected")).hexdigest()
        original = archive["cases"][name]
        assert oracle == original["expected_sha256"] and case["stdin_sha256"] == original["stdin_sha256"]
        assert binaries[name]["source_sha256"] == archive["built"][name]["source_sha256"]
        case.update(expected_sha256=oracle, elf_sha256=binaries[name]["elf_sha256"],
                    source_sha256=binaries[name]["source_sha256"])
    for problem in protocol["groups"]:
        calibration = [c for n, c in archive["cases"].items()
                       if c["role"] == "calibration" and n != "contest-" + problem]
        assert len(calibration) == 35
        rows = [(analyze_v2.features(c["probe"][0]), statistics.median(c["native_cpu_s"]))
                for c in calibration]
        frozen[problem] = {model: analyze_v2.fit(rows, analyze_v2.MODELS[model])[0]
                           for model in protocol["models"]}
    (out / "plan.json").write_text(json.dumps(dict(protocol=protocol, cases=cases, weights=frozen,
        compiler=subprocess.check_output(["g++", "--version"], text=True).splitlines()[0],
        flags=capacity.CFLAGS, calibration_sha256=digest(STUDY / "results/v2/results.json")), indent=2) + "\n")
    for src in ["collect.py", "tests/contest/problems.py", "experiments/capacity_misses/collect.py",
                "experiments/capacity_misses/repeatability.py"]:
        dest = out / src
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / src, dest)
    for src in ("guest.py", "guest.sh"):
        shutil.copy(HERE / src, out / src)
    shutil.copytree(args.runtime, out / "dynamorio")
    shutil.copyfile(args.client, out / "probe.so")


if __name__ == "__main__":
    main()
