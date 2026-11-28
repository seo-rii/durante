"""Rebuild the paired full/reduced payload from the registered shortest-paths graph study."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
GRAPH = HERE.parent / "aonohako_shortest_paths"
spec = importlib.util.spec_from_file_location("graph_payload", GRAPH / "prepare.py")
original = importlib.util.module_from_spec(spec)
spec.loader.exec_module(original)
sha = original.sha


def read(path):
    return json.loads(path.read_bytes())


def build(parent, client, target):
    old = read(GRAPH / "registration.json")
    original_plan = read(GRAPH / "frozen.json")
    if sha((GRAPH / "frozen.json").read_bytes()) != old["payload"]["plan.json"]:
        raise ValueError("different graph plan")
    target.mkdir()
    for name, digest in old["payload"].items():
        original.checked(parent / name, digest)
        if name in ("plan.json", "guest.py"):
            continue
        destination = target / ("full.so" if name == "probe.so" else name)
        destination.parent.mkdir(exist_ok=True, parents=True)
        shutil.copy2(parent / name, destination)
    for name in ("guest.py", "counters.py"):
        shutil.copyfile(HERE / name, target / name)
    shutil.copyfile(client, target / "reduced.so")
    frozen = dict(protocol=read(HERE / "protocol.json"), cases=original_plan["cases"], reference="reference",
                  deadline_us=65000, ml4=original_plan["ml4"], clients={"full":old["payload"]["probe.so"],
                        "reduced":sha(client.read_bytes())}, source_files={})
    for name in ("client/durante.c", "experiments/l1d_probe/probe.h", "experiments/l1d_probe/cache.h",
                 "experiments/l1d_probe/outer.h", "experiments/l1d_probe/CMakeLists.txt"):
        frozen["source_files"][name] = sha((ROOT / name).read_bytes())
    (target / "plan.json").write_text(json.dumps(frozen, indent=2) + "\n")


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    for key in ("graph-payload", "client", "output"):
        cli.add_argument("--" + key, required=True, type=Path)
    args = cli.parse_args()
    build(args.graph_payload, args.client, args.output)
    original.package(args.output, args.output.with_suffix(".tar.gz"), args.output.with_suffix(".registration.json"))
