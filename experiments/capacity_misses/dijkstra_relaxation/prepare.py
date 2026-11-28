"""Repackage the published Milan ELFs and instrumentation with the paired DAGs."""
import argparse
import json
from pathlib import Path
import runpy
import shutil
import subprocess

from inputs import answer, encode

HERE = Path(__file__).resolve().parent
CAP = HERE.parent
TOOLS = CAP / "range_distinct"
package = runpy.run_path(str(CAP / "aonohako_shortest_paths/prepare.py"))
SOLVERS = {"ref": "contest-dijkstra", "heap": "dijkstra--vecvec_getchar_pq", "set": "dijkstra--set_decrease_key"}


def read(p):
    return json.loads(p.read_bytes())


def build(tools, milan, out):
    protocol = read(HERE / "protocol.json")
    original_tools = read(TOOLS / "registration.json")["payload"]
    original_elfs = package["parent_files"]()
    frozen = read(TOOLS / "frozen.json")
    out.mkdir()
    for name, digest in original_tools.items():
        if name == "plan.json" or name.startswith(("bin/", "inputs/")):
            continue
        package["checked"](tools / name, digest)
        dest = out / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(tools / name, dest)
    (out / "bin").mkdir()
    (out / "inputs").mkdir()
    cases = {}
    for order, names in protocol["groups"].items():
        stdin = encode(order)
        path = "inputs/" + order
        (out / path).write_bytes(stdin)
        for n in names:
            solver = SOLVERS[n.split("-")[1]]
            original = "bin/" + solver
            digest = original_elfs[original]
            package["checked"](milan / original, digest)
            dest = out / "bin" / n
            shutil.copy2(milan / original, dest)
            if subprocess.check_output([milan / original], input=stdin, timeout=30) != answer():
                raise ValueError("wrong answer: " + n)
            cases[n] = dict(stdin=path, stdin_sha256=package["sha"](stdin),
                            expected_sha256=package["sha"](answer()), elf_sha256=digest)
    plan = dict(protocol=protocol, cases=cases, ml4=frozen["ml4"], clients=frozen["clients"],
                gshare_miss_ns=frozen["gshare_miss_ns"], source_sha256={
                    n: package["sha"]((HERE / n).read_bytes()) for n in ("prepare.py", "inputs.py")})
    (out / "plan.json").write_text(json.dumps(plan, indent=2) + "\n")
    package["package"](out, out.with_suffix(".tar.gz"), out.with_suffix(".registration.json"))


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--registered-tools", type=Path, required=True)
    cli.add_argument("--registered-milan", type=Path, required=True)
    cli.add_argument("--output", type=Path, required=True)
    args = cli.parse_args()
    build(args.registered_tools, args.registered_milan, args.output)
