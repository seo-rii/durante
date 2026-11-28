"""Repack the frozen stage payload and one experimental non-flat control client."""
import argparse
import importlib.util
import json
from pathlib import Path
import shutil

HERE = Path(__file__).resolve().parent
STAGES = HERE.parent / "graph_stage_cost"
spec = importlib.util.spec_from_file_location("stage_build", STAGES / "prepare.py")
stages = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stages)
graph = stages.graph


def build(stage_payload, client, dest):
    reg = json.loads((STAGES / "registration.json").read_bytes())
    prior = json.loads(graph.checked(STAGES / "frozen.json", reg["payload"]["plan.json"]))
    dest.mkdir()
    for name, digest in reg["payload"].items():
        if name == "plan.json":
            continue
        graph.checked(stage_payload / name, digest)
        target = dest / {"guest.py":"base_guest.py", "probe.so":"original.so"}.get(name, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(stage_payload / name, target)
    shutil.copyfile(client, dest / "callback.so")
    shutil.copyfile(HERE / "guest.py", dest / "guest.py")
    plan = dict(cases=prior["cases"], protocol=json.loads((HERE / "protocol.json").read_bytes()),
                clients={"original.so":prior["client_sha256"], "callback.so":graph.sha(client.read_bytes())},
                build_inputs={str(p.relative_to(HERE.parents[2])):graph.sha(p.read_bytes())
                              for p in (HERE.parents[2] / "client/durante.c", HERE / "CMakeLists.txt")})
    (dest / "plan.json").write_text(json.dumps(plan, indent=2) + "\n")
    graph.package(dest, dest.with_suffix(".tar.gz"), dest.with_suffix(".registration.json"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("stage-payload", "client", "output"):
        parser.add_argument("--" + key, required=True, type=Path)
    args = parser.parse_args()
    build(args.stage_payload, args.client, args.output)
