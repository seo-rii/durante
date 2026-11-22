"""Verify the registered inputs, CPU accounting and profile provenance of every first-run trial."""
import argparse
import json
from pathlib import Path
import sys
import tarfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from cpp20_locality.verify import read_compressed, require, sha, verify_collection, verify_elfs
from first_run_sampler.cases import PROGRAMS, manifest
from first_run_sampler.prepare import FAST, control_binary, study_files
from first_run_sampler.profile import capture_for_digest, resolve_for_digest
from first_run_sampler.schedule import build

RESULT = HERE.parent / "results/gce-first-run-sampler"


def verify_accounting(row):
    arm = row["arm"]
    fields = ("parent_cpu_ns", "hash_cpu_ns", "build_cpu_ns", "lookup_cpu_ns", "workflow_cpu_ns", "workflow_wall_ns")
    require(all(type(row[k]) is int and row[k] >= 0 for k in fields), "invalid operational CPU or wall time")
    hash_cpu, build_cpu, lookup_cpu = (row[k] for k in ("hash_cpu_ns", "build_cpu_ns", "lookup_cpu_ns"))
    require(hash_cpu > 0 and (build_cpu > 0) == (arm == "calibration")
            and (lookup_cpu > 0) == (arm == "profiled"), "wrong operation charged for arm")
    require(hash_cpu + build_cpu + lookup_cpu <= row["parent_cpu_ns"], "overlapping parent CPU components")
    require(row["workflow_cpu_ns"] == row["cpu_us"] * 1000 + row["parent_cpu_ns"],
            "workflow CPU differs from child plus parent")
    require(row["workflow_wall_ns"] + 500 >= row["wall_us"] * 1000, "child interval exceeds workflow wall")


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
    totals = verify_collection(result, protocol, cases, sampled, sampled | {"live"},
                               extra_receipts_by_arm={"calibration": {"profile.json"}})
    data = read_compressed(result / "dataset.json.gz")
    by_case = {c["case_id"]: c for c in cases}
    profiles = {}
    with tarfile.open(result / "receipts.tar.xz", "r:xz") as receipts:
        for row in data["rows"]:
            verify_accounting(row)
            case = by_case[row["case_id"]]
            digest = case["binary_sha256"]
            require(row["elf_sha256"] == digest, "preflight ELF mismatch")
            if row["arm"] == "calibration":
                prefix = f"runs/{row['sequence']:03d}/"
                raw = receipts.extractfile(prefix + "sample.json").read()
                expected = capture_for_digest(digest, case["stdin"].encode(), raw, row["launcher_pid"], arm="full")
                name = prefix + "profile.json"
                blob = receipts.extractfile(name).read()
                require(json.loads(blob) == expected and digest not in profiles, "profile differs from first full sample")
                resolve_for_digest(expected, digest)
                profiles[digest] = (name, sha(blob))
            if row["arm"] in ("calibration", "profiled"):
                require((row["profile_path"], row["profile_sha256"]) == profiles[digest], "wrong cached profile")
            else:
                require("profile_path" not in row and "profile_sha256" not in row, "unexpected cached profile")
    return totals


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("result", type=Path, default=RESULT, nargs="?")
    cli.add_argument("--elf-archive", type=Path, help="Aonohako #154 corpus/elfs.tar.xz")
    cli.add_argument("--upstream-cases", type=Path, help="Aonohako #154 corpus/cases.json")
    args = cli.parse_args()
    trials, records, clocks = verify(args.result, args.elf_archive, args.upstream_cases)
    print(f"Verified {trials} pinned runs, {records} v11 records and {clocks} terminal clocks.")
