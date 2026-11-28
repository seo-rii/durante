"""Build a new query panel and reuse the complete frozen branch and cache instrumentation."""
import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess

from inputs import encode, oracle, workload

HERE = Path(__file__).resolve().parent
CAP = HERE.parent
PARENT = CAP / "order_statistics_transfer"
spec = importlib.util.spec_from_file_location("registered_package", CAP / "aonohako_shortest_paths/prepare.py")
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)


def read(p):
    return json.loads(p.read_bytes())


def build(old, out, compiler):
    protocol = read(HERE / "protocol.json")
    origin = read(PARENT / "registration.json")["payload"]
    parent = read(PARENT / "frozen.json")
    out.mkdir()
    for name, digest in origin.items():
        if name == "plan.json" or name.startswith(("bin/", "inputs/")):
            continue
        package.checked(old / name, digest)
        dest = out / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(old / name, dest)
    (out / "inputs").mkdir()
    (out / "bin").mkdir()
    cases, binaries = {}, {}
    for order, solvers in protocol["groups"].items():
        a, queries = workload(order)
        stdin, expected = encode(a, queries), oracle(a, queries)
        stdin_name = "inputs/" + order
        (out / stdin_name).write_bytes(stdin)
        for name in solvers:
            solver = name.split("-")[1]
            exe = out / "bin" / name
            if solver in binaries:
                shutil.copy2(binaries[solver], exe)
            else:
                subprocess.run([compiler, *parent["flags"], "-DSOLVER_" + solver.upper(),
                                HERE / "queries.cpp", "-o", exe], check=True)
                binaries[solver] = exe
            if subprocess.check_output([exe], input=stdin, timeout=30) != expected:
                raise ValueError("wrong answer in query order: " + name)
            cases[name] = dict(stdin=stdin_name, stdin_sha256=package.sha(stdin), expected_sha256=package.sha(expected),
                               elf_sha256=package.sha(exe.read_bytes()))
    plan = dict(protocol=protocol, cases=cases, ml4=parent["ml4"], clients=parent["clients"], flags=parent["flags"],
                gshare_miss_ns=parent["gshare"]["gshare_mispredicts"],
                compiler=subprocess.check_output([compiler, "-dumpfullversion"], text=True).strip(),
                source_sha256={p:package.sha((HERE / p).read_bytes()) for p in ("inputs.py","queries.cpp","prepare.py")})
    (out / "plan.json").write_text(json.dumps(plan, indent=2) + "\n")
    package.package(out, out.with_suffix(".tar.gz"), out.with_suffix(".registration.json"))


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--registered-tools", type=Path, required=True)
    cli.add_argument("--output", type=Path, required=True)
    cli.add_argument("--compiler", default="g++")
    args = cli.parse_args()
    build(args.registered_tools, args.output, args.compiler)
