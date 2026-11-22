"""Replay frozen payload identities, all v11 clocks and per-trial placement."""
import argparse
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from cpp20_locality.verify import require, sha, verify_collection, verify_elfs
from live_sampler_fast.prepare import BASE, COST, study_files
from live_sampler_fast.schedule import build
from live_sampler_fast.profiles import fixtures, profiles

RESULT = HERE.parent / "results/gce-live-sampler-fast"


def verify(result, elf_archive=None):
    protocol = (HERE / "protocol.json").read_bytes()
    plan = json.loads(protocol)
    tools = plan["tools_sha256"]
    cases = json.loads((HERE / "cases.json").read_bytes())
    require(cases == fixtures(), "case generation differs from the frozen inputs")
    require(json.loads((HERE / "profiles.json").read_bytes()) == profiles(), "selection differs from training receipts")
    original_cases = json.loads((BASE / "cases.json").read_bytes())
    registered = {case["case_id"]: case for case in cases}
    for case in original_cases:
        entry = registered[case["case_id"]]
        require(all(entry[k] == case[k] for k in ("program", "binary_sha256", "stdin", "stdout")),
                "changed repeated fixture")
    require(len(registered) == len(cases) == 10 and plan["schedule"] == build(cases), "wrong schedule or cases")
    require(plan["poll_interval_ms"] == 5 and plan["work_budget"] == 100_000_000_000
            and plan["trial_timeout_s"] == 120 and plan["cpu"] == 1, "changed measurement conditions")
    for name, source in study_files().items():
        require(sha(source.read_bytes()) == tools[name], "changed collector input: " + name)
    previous = json.loads((COST / "protocol.json").read_bytes())["tools_sha256"]
    inherited = {n for n in previous if n.startswith(("bin/", "dynamorio/", "base/"))}
    inherited.update(("guest.sh", "production_reader.py", "v11.so", "gate.so", "span.so"))
    require(all(previous[k] == tools[k] for k in inherited), "changed original binary, runtime, or control")
    require(previous["bench.py"] == tools["cost_bench.py"], "wrong live receipt collector")
    require(all(tools["bin/" + c["program"]] == c["binary_sha256"] for c in cases), "wrong input executable")
    if elf_archive:
        verify_elfs(elf_archive, cases)
    json_arms = {"catalog", "gate", "full", "profiled"}
    return verify_collection(result, protocol, cases, json_arms, json_arms | {"live", "packed"},
                             binary_sample_arms={"packed"})


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("result", type=Path, default=RESULT, nargs="?")
    cli.add_argument("--elf-archive", type=Path, help="Aonohako #154 elfs.tar.xz")
    args = cli.parse_args()
    trials, records, clocks = verify(args.result, args.elf_archive)
    print(f"Verified {trials} pinned runs, {records} canonical v11 reports and {clocks} terminal live clocks.")
