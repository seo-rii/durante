"""Verify the registered inputs, canonical v11 records and CPU placement of every first-run trial."""
import argparse
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from cpp20_locality.verify import require, sha, verify_collection, verify_elfs
from first_run_sampler.cases import PROGRAMS, manifest
from first_run_sampler.prepare import FAST, control_binary, study_files
from first_run_sampler.schedule import build

RESULT = HERE.parent / "results/gce-first-run-sampler"


def verify(result, elf_archive=None, upstream_cases=None):
    protocol = (HERE / "protocol.json").read_bytes()
    plan = json.loads(protocol)
    tools = plan["tools_sha256"]
    cases = json.loads((HERE / "cases.json").read_bytes())
    require(plan["schedule"] == build(cases) and len(plan["schedule"]) == 186, "wrong schedule")
    require(plan["cpu"] == 1 and plan["poll_interval_ms"] == 5 and plan["trial_timeout_s"] == 120
            and plan["work_budget"] == 100_000_000_000, "changed live-clock conditions")
    for name, source in study_files().items():
        require(sha(source.read_bytes()) == tools[name], "changed collector: " + name)
    previous = json.loads((FAST / "protocol.json").read_bytes())["tools_sha256"]
    inherited = {k for k in previous if k.startswith("dynamorio/")}
    inherited.update(("guest.sh", "base/bench.py", "cost_bench.py", "production_reader.py", "v11.so", "span.so",
                      "profiled.so"))
    require(all(tools[k] == previous[k] for k in inherited), "changed live runtime or measurement clients")
    require(sha(control_binary()) == tools["bin/path_switch"], "wrong control ELF")
    real = [c for c in cases if c["population"] == "real"]
    require({c["case_id"] for c in real} == {f"{p}/{s}" for p, scales in PROGRAMS.items() for s in scales},
            "wrong Aonohako inputs")
    if elf_archive:
        verify_elfs(elf_archive, real)
    control_cases = HERE / "control/cases.json"
    if upstream_cases:
        require(cases == manifest(upstream_cases, control_cases), "upstream case manifest mismatch")
    control = [{k: v for k, v in c.items() if k not in ("split", "population")} for c in cases
               if c["population"] == "control"]
    require(control == json.loads(control_cases.read_bytes()), "changed path-switch inputs or oracle")
    for c in cases:
        require(c["binary_sha256"] == tools["bin/" + c["program"]]
                and all(sha(c[k].encode()) == c[k + "_sha256"] for k in ("stdin", "stdout")), "wrong case digest")

    sampled = {"calibration", "full", "profiled"}
    return verify_collection(result, protocol, cases, sampled, sampled | {"live"},
                             extra_receipts_by_arm={"calibration": {"profile.json"}})


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("result", type=Path, default=RESULT, nargs="?")
    cli.add_argument("--elf-archive", type=Path, help="Aonohako #154 corpus/elfs.tar.xz")
    cli.add_argument("--upstream-cases", type=Path, help="Aonohako #154 corpus/cases.json")
    args = cli.parse_args()
    trials, records, clocks = verify(args.result, args.elf_archive, args.upstream_cases)
    print(f"Verified {trials} pinned runs, {records} v11 records and {clocks} terminal clocks.")
