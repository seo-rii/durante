"""Build three C++20 solvers and freeze two new predecessor inputs and their output."""
import argparse
import bisect
import importlib.util
import json
from pathlib import Path
import random
import shutil
import subprocess

HERE = Path(__file__).resolve().parent
MILAN = HERE.parent / "milan"
spec = importlib.util.spec_from_file_location("pinned_payload", HERE.parent / "aonohako_shortest_paths/prepare.py")
payload = importlib.util.module_from_spec(spec)
spec.loader.exec_module(payload)
FLAGS = ["-std=c++20", "-O2", "-static", "-s", "-Wl,--build-id=none"]


def workload(protocol, pattern):
    """Each ordering uses the same initial inventory and multiset of budgets."""
    rng = random.Random(protocol["seed"])
    largest = protocol["max_price"]
    prices = [rng.randint(1, largest) for _ in range(protocol["count"])]
    budgets = [rng.randrange(largest + 2) for _ in range(protocol["queries"])]
    if pattern == "clustered":
        for start in range(0, len(budgets), 512):
            budgets[start:start + 512] = sorted(budgets[start:start + 512], reverse=(start // 512) % 2 == 1)
    elif pattern != "uniform":
        raise ValueError("unknown budget ordering")
    return prices, budgets


def oracle(prices, budgets):
    # A sorted list and literal deletion shift the entire suffix; this is
    # independent of the tree counts, node erasure and disjoint-set algorithms.
    available = sorted(prices)
    result = []
    for budget in budgets:
        pos = bisect.bisect_right(available, budget)
        result.append(available.pop(pos - 1) if pos else -1)
    return "".join(f"{v}\n" for v in result).encode()


def encode(prices, budgets):
    return (f"{len(prices)} {len(budgets)}\n" + " ".join(map(str, prices)) + "\n"
            + " ".join(map(str, budgets)) + "\n").encode()


def build(args):
    protocol = json.loads((HERE / "protocol.json").read_bytes())
    out = args.output
    out.mkdir()
    base = payload.parent_files()
    for name, digest in base.items():
        if name == "plan.json" or name.startswith(("bin/", "inputs/")):
            continue
        original = args.corrected / name
        payload.checked(original, digest)
        dest = out / name
        dest.parent.mkdir(exist_ok=True, parents=True)
        shutil.copy2(original, dest)
    (out / "bin").mkdir()
    (out / "inputs").mkdir()
    prior = json.loads((MILAN / "frozen.json").read_bytes())["weights"]["dijkstra"]
    weights = {model: prior[model] for model in protocol["models"]}
    cases = {}
    for pattern in protocol["inputs"]:
        prices, budgets = workload(protocol, pattern)
        data = encode(prices, budgets)
        expected = oracle(prices, budgets)
        (out / "inputs" / pattern).write_bytes(data)
        for solver in protocol["solvers"]:
            name = pattern + "-" + solver
            binary = out / "bin" / name
            if pattern == protocol["inputs"][0]:
                subprocess.run([args.compiler, *FLAGS, "-DSOLVER_" + solver.upper(), HERE / "solve.cpp", "-o", binary], check=True)
            else:
                shutil.copy2(out / "bin" / (protocol["inputs"][0] + "-" + solver), binary)
            if subprocess.check_output([binary], input=data) != expected:
                raise ValueError("oracle disagreement: " + name)
            cases[name] = dict(elf_sha256=payload.sha(binary.read_bytes()), stdin="inputs/" + pattern,
                               stdin_sha256=payload.sha(data), expected_sha256=payload.sha(expected))
    plan = dict(protocol=protocol, cases=cases, weights={p: weights for p in protocol["inputs"]},
                compiler=subprocess.check_output([args.compiler, "--version"], text=True).splitlines()[0],
                flags=FLAGS, solver_sha256=payload.sha((HERE / "solve.cpp").read_bytes()),
                preparer_sha256=payload.sha(Path(__file__).read_bytes()), probe_sha256=base["probe.so"])
    (out / "plan.json").write_text(json.dumps(plan, indent=2) + "\n")
    payload.package(out, args.archive, args.registration)
    registration = json.loads(args.registration.read_bytes())
    registration["files"] = [dict(path=name, sha256=digest) for name, digest in registration.pop("payload").items()]
    args.registration.write_text(json.dumps(registration, indent=2) + "\n")


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    for key in ("corrected", "output", "archive", "registration"):
        cli.add_argument("--" + key, type=Path, required=True)
    cli.add_argument("--compiler", default="g++")
    build(cli.parse_args())
