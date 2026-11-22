"""Collect cold profile construction and subsequent live-clock workflow costs on one CPU."""
import hashlib
import json
import os
from pathlib import Path
import platform
import signal
import time

import cost_bench as previous
from profile import capture_for_digest, resolve_for_digest

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "output"
require = previous.require


def trial(item, index, case, profiles, plan):
    directory = OUT / "runs" / f"{index:03d}"
    directory.mkdir()
    program = ROOT / "bin" / case["program"]
    stdin = OUT / "inputs" / case["stdin_sha256"]
    arm = item["arm"]
    require(arm in ("calibration", "live", "full", "profiled"), "unknown arm")
    build_cpu = lookup_cpu = 0
    profile_bytes = None
    profile_path = None
    wall_start = time.monotonic_ns()
    parent_start = time.process_time_ns()
    hash_start = time.process_time_ns()
    elf_hash = previous.digest(program)
    require(elf_hash == case["binary_sha256"], "ELF preflight mismatch")
    hash_cpu = time.process_time_ns() - hash_start
    argv = previous.command("live", program, directory)
    if arm != "live":
        client = ROOT / ("profiled.so" if arm == "profiled" else "span.so")
        options = "-out " + previous.quoted(directory / "sample.json")
        if arm == "profiled":
            lookup_start = time.process_time_ns()
            profile_path = profiles[elf_hash]
            profile_bytes = profile_path.read_bytes()
            options += " -sites " + resolve_for_digest(json.loads(profile_bytes), elf_hash)
            lookup_cpu = time.process_time_ns() - lookup_start
        position = argv.index("-follow_children")
        argv[position:position] = ["-client", client, "00000001", options]
    row = previous.base["execute"](argv, directory, stdin, plan)
    if arm == "calibration":
        build_start = time.process_time_ns()
        require(elf_hash not in profiles, "duplicate first execution")
        profile = capture_for_digest(elf_hash, stdin.read_bytes(), (directory / "sample.json").read_bytes(),
                                     row["launcher_pid"], arm="full")
        profile_path = directory / "profile.json"
        profile_bytes = (json.dumps(profile, indent=2) + "\n").encode()
        with profile_path.open("xb") as output:
            output.write(profile_bytes)
        profiles[elf_hash] = profile_path
        build_cpu = time.process_time_ns() - build_start
    parent_cpu = time.process_time_ns() - parent_start
    workflow_wall = time.monotonic_ns() - wall_start
    require(0 < hash_cpu and hash_cpu + lookup_cpu + build_cpu <= parent_cpu, "owner CPU accounting mismatch")
    require(row["stdout_sha256"] == case["stdout_sha256"] and row["cpu_us"] > 0, "invalid execution")
    previous.finish_live("live", directory, row["launcher_pid"])
    if arm != "live":
        sample = json.loads((directory / "sample.json").read_bytes())
        require(sample["schema"] == 2 and sample["pid"] == row["launcher_pid"] and sample["threads"] == 1
                and sample["line_bytes"] == 64 and sample["period"] == 1024, "wrong sampled execution")
    if profile_path is not None:
        row.update(profile_path=profile_path.relative_to(OUT).as_posix(),
                   profile_sha256=hashlib.sha256(profile_bytes).hexdigest())
    receipts = {p.relative_to(OUT).as_posix(): previous.digest(p) for p in sorted(directory.rglob("*")) if p.is_file()}
    row.update(sequence=index, **item, receipts=receipts, elf_sha256=elf_hash, hash_cpu_ns=hash_cpu,
               lookup_cpu_ns=lookup_cpu, build_cpu_ns=build_cpu, parent_cpu_ns=parent_cpu,
               workflow_cpu_ns=row["cpu_us"] * 1000 + parent_cpu, workflow_wall_ns=workflow_wall)
    return row


def main():
    plan = json.loads((ROOT / "protocol.json").read_bytes())
    for name, expected in plan["tools_sha256"].items():
        require(previous.digest(ROOT / name) == expected, "changed frozen payload: " + name)
    cpu = plan["cpu"]
    cgroup = Path("/sys/fs/cgroup") / Path("/proc/self/cgroup").read_text().strip().split(":")[-1].lstrip("/")
    allowed = (cgroup / "cpuset.cpus.effective").read_text().strip()
    require(os.sched_getaffinity(0) == {cpu} and allowed == str(cpu), "unexpected CPU placement")
    cases = {c["case_id"]: c for c in json.loads((ROOT / "cases.json").read_bytes())}
    OUT.mkdir()
    (OUT / "inputs").mkdir()
    (OUT / "runs").mkdir()
    for c in cases.values():
        stdin = OUT / "inputs" / c["stdin_sha256"]
        stdin.write_bytes(c["stdin"].encode())
        require(previous.digest(stdin) == c["stdin_sha256"], "input digest mismatch")
    start = previous.base["steal"](cpu)
    profiles = {}
    rows = []
    with (OUT / "progress.jsonl").open("x") as progress:
        for index, item in enumerate(plan["schedule"]):
            row = trial(item, index, cases[item["case_id"]], profiles, plan)
            rows.append(row)
            progress.write(json.dumps(row) + "\n")
            progress.flush()
    model = next(s.split(":", 1)[1].strip() for s in Path("/proc/cpuinfo").read_text().splitlines()
                 if s.startswith("model name"))
    config = dict(cpu=cpu, affinity=sorted(os.sched_getaffinity(0)), cgroup_allowed_cpus=allowed,
                  kernel=platform.release(), cpu_model=model, clock_tick_hz=os.sysconf("SC_CLK_TCK"),
                  cpu_steal_ticks=previous.base["steal"](cpu) - start)
    previous.save(OUT / "dataset.json", dict(schema=1, protocol_sha256=previous.digest(ROOT / "protocol.json"),
                                             platform=config, rows=rows))


if __name__ == "__main__":
    signal.signal(signal.SIGALRM, previous.base["timeout"])
    main()
