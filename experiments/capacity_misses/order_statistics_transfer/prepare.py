"""Build the inversion holdout and reuse the exact published predecessor and diagnostic binaries."""
import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from inputs import encode, oracle, workload

HERE = Path(__file__).resolve().parent
CAP = HERE.parent
PRED = CAP / "predecessor"
GSHARE = CAP / "milan/gshare"
FLAT = CAP / "flat_cache"

spec = importlib.util.spec_from_file_location("graph_package", CAP / "aonohako_shortest_paths/prepare.py")
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)
FLAGS = ["-std=c++20", "-O2", "-static", "-s", "-Wl,--build-id=none"]


def read(path):
    return json.loads(path.read_bytes())


def build(args):
    proto = read(HERE / "protocol.json")
    source = read(PRED / "frozen.json")
    reg = {r["path"]: r["sha256"] for r in read(PRED / "registration.json")["files"]}
    target = args.output
    target.mkdir()
    for name,digest in reg.items():
        if name in ("guest.py", "plan.json"):
            continue
        original = args.predecessor / name
        package.checked(original, digest)
        dest = target / ("original.so" if name == "probe.so" else name)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(original, dest)
    flat_hash = read(FLAT / "frozen.json")["clients"]["flat.so"]
    branch_hash = read(GSHARE / "records.json")["tools_sha256"]["probe"]
    for binary, name, expected in ((args.flat, "flat.so", flat_hash),(args.branch, "branch.so", branch_hash)):
        package.checked(binary, expected)
        shutil.copy2(binary, target / name)
    shutil.copy2(HERE / "guest.py", target / "guest.py")
    stage = CAP / "graph_stage_cost/guest.py"
    shutil.copy2(stage, target / "base_guest.py")
    cases = dict(source["cases"])
    gold = {}
    for group in proto["holdout"]:
        values = workload(group)
        data, expected = encode(values), oracle(values)
        stdin = "inputs/" + group
        (target / stdin).write_bytes(data)
        for name in proto["groups"][group]:
            solver = name.split("-")[1]
            exe = target / "bin" / name
            if solver not in gold:
                subprocess.run([args.compiler, *FLAGS, "-DSOLVER_" + solver.upper(), HERE / "inversions.cpp",
                                "-o", exe], check=True)
                gold[solver] = exe
            else:
                shutil.copy2(gold[solver], exe)
            # The package destination can be mounted noexec. Validate the
            # actual packaged bytes from a temporary executable directory.
            with tempfile.TemporaryDirectory() as tmp:
                check = Path(tmp) / "solver"
                shutil.copy2(exe, check)
                if subprocess.run([check], input=data, capture_output=True, check=True).stdout != expected:
                    raise ValueError("wrong inversion count: " + name)
            cases[name] = dict(stdin=stdin, stdin_sha256=package.sha(data), elf_sha256=package.sha(exe.read_bytes()),
                               expected_sha256=package.sha(expected))
    ml4 = read(CAP / "milan/frozen.json")["weights"]["dijkstra"]["ML4"]
    if any(group["ML4"] != ml4 for group in source["weights"].values()):
        raise ValueError("wrong original predecessor weight vector")
    plan = dict(protocol=proto, cases=cases, ml4=ml4,
                gshare=read(GSHARE / "protocol.json")["models"]["with_gshare"],
                clients={"original.so":reg["probe.so"],"flat.so":flat_hash,"branch.so":branch_hash},
                source_sha256={n:package.sha((HERE/n).read_bytes()) for n in ("guest.py","inputs.py","inversions.cpp")},
                compiler=subprocess.check_output([args.compiler,"-dumpfullversion"],text=True).strip(), flags=FLAGS)
    (target / "plan.json").write_text(json.dumps(plan, indent=2) + "\n")
    package.package(target, target.with_suffix(".tar.gz"), target.with_suffix(".registration.json"))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("predecessor","flat","branch","output"):
        p.add_argument("--"+name, type=Path, required=True)
    p.add_argument("--compiler", default="g++")
    build(p.parse_args())
