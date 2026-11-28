"""Prepare the original three-program graph panel with two full-cache clients."""
import argparse
import importlib.util
import json
from pathlib import Path
import shutil

HERE = Path(__file__).resolve().parent
CONTROL = HERE.parent / "graph_block_control"
spec = importlib.util.spec_from_file_location("graph_control_build", CONTROL / "prepare.py")
control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control)
graph = control.graph
SOURCES = ("client/durante.c", "client/latency_bound.h", "experiments/l1d_probe/probe.h",
           "experiments/l1d_probe/cache.h", "experiments/l1d_probe/outer.h",
           "experiments/capacity_misses/flat_cache/CMakeLists.txt")


def build(payload, client, target):
    reg = json.loads((CONTROL / "registration.json").read_bytes())
    prior = json.loads(graph.checked(CONTROL / "frozen.json", reg["payload"]["plan.json"]))
    target.mkdir()
    for name, digest in reg["payload"].items():
        if name in ("callback.so", "plan.json"):
            continue
        graph.checked(payload / name, digest)
        dest = target / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(payload / name, dest)
    shutil.copy2(client, target / "flat.so")
    plan = dict(cases=prior["cases"], protocol=json.loads((HERE / "protocol.json").read_bytes()),
                clients={"original.so": reg["payload"]["original.so"], "flat.so": graph.sha(client.read_bytes())},
                build_inputs={n: graph.sha((HERE.parents[2] / n).read_bytes()) for n in SOURCES})
    (target / "plan.json").write_text(json.dumps(plan, indent=2) + "\n")
    graph.package(target, target.with_suffix(".tar.gz"), target.with_suffix(".registration.json"))


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    for option in ("control-payload", "client", "output"):
        cli.add_argument("--" + option, required=True, type=Path)
    args = cli.parse_args()
    build(args.control_payload, args.client, args.output)
