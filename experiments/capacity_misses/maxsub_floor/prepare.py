"""Build three maximum-subarray solvers and package the registered cache, branch and clock diagnostics."""
import argparse
import json
from pathlib import Path
import runpy
import shutil
import subprocess

HERE = Path(__file__).resolve().parent
CAP = HERE.parent
OLDER = CAP / "range_distinct"
package = runpy.run_path(str(CAP / "aonohako_shortest_paths/prepare.py"))
inputs = runpy.run_path(str(HERE / "inputs.py"))


def read(path):
    return json.loads(path.read_bytes())


def build(tools, out, compiler):
    protocol = read(HERE / "protocol.json")
    base = read(OLDER / "frozen.json")
    reg = read(OLDER / "registration.json")["payload"]
    out.mkdir()
    for name, digest in reg.items():
        if name == "plan.json" or name.startswith(("bin/", "inputs/")):
            continue
        package["checked"](tools / name, digest)
        dst = out / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(tools / name, dst)
    (out / "bin").mkdir()
    (out / "inputs").mkdir()
    cases, binaries = {}, {}
    for family, names in protocol["groups"].items():
        count, seed = inputs["workload"](family)
        stdin, answer = inputs["encode"](count, seed), inputs["answer"](count, seed)
        input_file = "inputs/" + family
        (out / input_file).write_bytes(stdin)
        for name in names:
            solver = name.split("-")[1]
            dst = out / "bin" / name
            if solver not in binaries:
                subprocess.run([compiler, *base["flags"], "-DSOLVER_" + solver.upper(), HERE / "maxsub.cpp", "-o", dst], check=True)
                binaries[solver] = dst
                for count, seed in ((1, 0), (2, 123), (17, 47), (100, 11)):
                    if subprocess.check_output([dst], input=inputs["encode"](count, seed), timeout=10) != inputs["answer"](count, seed):
                        raise ValueError("small oracle differs: " + name)
            else:
                shutil.copy2(binaries[solver], dst)
            if subprocess.check_output([dst], input=stdin, timeout=30) != answer:
                raise ValueError("registered oracle differs: " + name)
            cases[name] = dict(stdin=input_file, stdin_sha256=package["sha"](stdin), expected_sha256=package["sha"](answer),
                               elf_sha256=package["sha"](dst.read_bytes()))
    plan = dict(protocol=protocol, cases=cases, ml4=base["ml4"], clients=base["clients"], gshare_miss_ns=base["gshare_miss_ns"],
                compiler=subprocess.check_output([compiler, "-dumpfullversion"], text=True).strip(), flags=base["flags"],
                source_sha256={n: package["sha"]((HERE / n).read_bytes()) for n in ("prepare.py", "inputs.py", "maxsub.cpp")})
    (out / "plan.json").write_text(json.dumps(plan, indent=2) + "\n")
    package["package"](out, out.with_suffix(".tar.gz"), out.with_suffix(".registration.json"))


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--registered-tools", type=Path, required=True)
    cli.add_argument("--output", type=Path, required=True)
    cli.add_argument("--compiler", default="g++")
    args = cli.parse_args()
    build(args.registered_tools, args.output, args.compiler)
