"""Assemble one registered probe, original ELF files and the staged graph guest."""
import argparse
import importlib.util
import json
from pathlib import Path
import shutil

HERE = Path(__file__).resolve().parent
PARENT = HERE.parent / "aonohako_shortest_paths"
spec = importlib.util.spec_from_file_location("registered_graph", PARENT / "prepare.py")
graph = importlib.util.module_from_spec(spec)
spec.loader.exec_module(graph)


def build(parent, target):
    registration = json.loads((PARENT / "registration.json").read_bytes())
    old = json.loads(graph.checked(PARENT / "frozen.json", registration["payload"]["plan.json"]))
    protocol = json.loads((HERE / "protocol.json").read_bytes())
    cases = {n: old["cases"][n] for n in protocol["programs"]}
    target.mkdir()
    for name, digest in registration["payload"].items():
        if name in ("guest.py", "plan.json") or (name.startswith("bin/") and name[4:] not in cases):
            continue
        graph.checked(parent / name, digest)
        dest = target / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(parent / name, dest)
    shutil.copyfile(HERE / "guest.py", target / "guest.py")
    plan = dict(protocol=protocol, cases=cases, client_sha256=old["corrected_probe_sha256"])
    (target / "plan.json").write_text(json.dumps(plan, indent=2) + "\n")
    graph.package(target, target.with_suffix(".tar.gz"), target.with_suffix(".registration.json"))


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--graph-payload", type=Path, required=True)
    cli.add_argument("--output", type=Path, required=True)
    args = cli.parse_args()
    build(args.graph_payload, args.output)
