"""Replay the live clocks, production counters, scheduler and payload identity."""
import argparse
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from cpp20_locality.fixtures import manifest
from cpp20_locality.verify import require, sha, verify_collection, verify_elfs
from live_sampler_cost.prepare import BASE, study_files
from live_sampler_cost.schedule import build

RESULT = HERE.parent / "results/gce-live-sampler-cost"
SAMPLERS = {"empty", "gate", "full", "fused"}


def verify(result, elf_archive=None):
    protocol = (HERE / "protocol.json").read_bytes()
    plan = json.loads(protocol)
    tools = plan["tools_sha256"]
    cases = manifest()
    previous = json.loads((BASE / "protocol.json").read_bytes())["tools_sha256"]
    require(cases == json.loads((BASE / "cases.json").read_bytes()), "changed fixture oracle")
    require(plan["schedule"] == build(cases), "schedule mismatch")
    require(plan["poll_interval_ms"] == 5 and plan["work_budget"] == 100_000_000_000
            and plan["trial_timeout_s"] == 120 and plan["cpu"] == 1, "changed live budget or measurement conditions")
    for name, source in study_files().items():
        require(sha(source.read_bytes()) == tools[name], "changed measurement source: " + name)
    inherited = {k for k in previous if k.startswith(("bin/", "dynamorio/"))}
    inherited.update(("cases.json", "guest.sh", "v11.so", "span.so", "production_reader.py"))
    require(all(tools[k] == previous[k] for k in inherited), "different release, sampler or canonical corpus")
    require(all(tools["bin/" + c["program"]] == c["binary_sha256"] for c in cases), "wrong ELF identity")
    if elf_archive:
        verify_elfs(elf_archive, cases)
    return verify_collection(result, protocol, cases, SAMPLERS, SAMPLERS | {"live"})


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("result", type=Path, default=RESULT, nargs="?")
    cli.add_argument("--elf-archive", type=Path, help="original elfs.tar.xz from Aonohako #154")
    args = cli.parse_args()
    trials, records, clocks = verify(args.result, args.elf_archive)
    print(f"Verified {trials} pinned runs, {records} canonical v11 reports and {clocks} terminal live clocks.")
