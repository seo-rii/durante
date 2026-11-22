"""Measure the all-site and frozen-profile live sampler paths using the original v11 launch."""
import json
import os
from pathlib import Path
import platform
import signal
import struct

import cost_bench as previous

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "output"
SIDECARS = {"catalog": "catalog.so", "gate": "gate.so", "full": "span.so",
            "packed": "packed.so", "profiled": "profiled.so"}
PACKED_HEADER = struct.Struct("<8s10Q")
PACKED_SITE = struct.Struct("<6Q")


def command(arm, case, directory, profiles):
    program = ROOT / "bin" / case["program"]
    if arm == "warmup":
        return [program]
    cmd = previous.command("live", program, directory)
    if arm == "live":
        return cmd
    sidecar = "sample.bin" if arm == "packed" else "sample.json"
    options = "-out " + previous.quoted(directory / sidecar)
    if arm == "profiled":
        elf_hash = previous.digest(program)
        previous.require(elf_hash == case["binary_sha256"], "profile input ELF changed")
        offsets = profiles["by_elf"][elf_hash]["offsets"]
        previous.require(1 <= len(offsets) <= 64 and all(type(n) is int and 0 <= n < 2**64 for n in offsets)
                         and offsets == sorted(set(offsets)), "invalid ELF profile")
        options += " -sites " + ",".join(str(n) for n in offsets)
    position = cmd.index("-follow_children")
    cmd[position:position] = ["-client", ROOT / SIDECARS[arm], "00000001", options]
    return cmd


def finish(arm, directory, pid):
    previous.finish_live("live", directory, pid)
    if arm == "live":
        return
    if arm == "packed":
        raw = (directory / "sample.bin").read_bytes()
        header = PACKED_HEADER.unpack_from(raw)
        previous.require(header[:6] == (b"DSPAN001", 2, pid, 1, 1024, 64)
                         and len(raw) == PACKED_HEADER.size + header[-1] * PACKED_SITE.size, "wrong packed receipt")
        return
    sample = json.loads((directory / "sample.json").read_bytes())
    previous.require(sample["pid"] == pid and sample["threads"] == 1, "wrong sampler process")
    if arm in ("catalog", "gate"):
        previous.require(sample["schema"] == 1 and sample["arm"] == arm, "wrong control receipt")
    else:
        previous.require(sample["schema"] == 2 and sample["line_bytes"] == 64, "wrong span receipt")
    if arm != "catalog":
        previous.require(sample["period"] == 1024, "wrong sampler period")


def main():
    plan = json.loads((ROOT / "protocol.json").read_bytes())
    for name, expected in plan["tools_sha256"].items():
        previous.require(previous.digest(ROOT / name) == expected, "changed input: " + name)
    cpu = plan["cpu"]
    cgroup = Path("/sys/fs/cgroup") / Path("/proc/self/cgroup").read_text().strip().split(":")[-1].lstrip("/")
    allowed = (cgroup / "cpuset.cpus.effective").read_text().strip()
    previous.require(os.sched_getaffinity(0) == {cpu} and allowed == str(cpu), "unexpected CPU placement")
    cases = {c["case_id"]: c for c in json.loads((ROOT / "cases.json").read_bytes())}
    profiles = json.loads((ROOT / "profiles.json").read_bytes())
    OUT.mkdir()
    (OUT / "inputs").mkdir()
    (OUT / "runs").mkdir()
    for c in cases.values():
        stdin = OUT / "inputs" / c["stdin_sha256"]
        stdin.write_bytes(c["stdin"].encode())
        previous.require(previous.digest(stdin) == c["stdin_sha256"]
                         and previous.digest(ROOT / "bin" / c["program"]) == c["binary_sha256"], "fixture mismatch")
    start = previous.base["steal"](cpu)
    rows = []
    with (OUT / "progress.jsonl").open("x") as progress:
        for index, trial in enumerate(plan["schedule"]):
            case, arm = cases[trial["case_id"]], trial["arm"]
            directory = OUT / "runs" / f"{index:03d}"
            directory.mkdir()
            args = command(arm, case, directory, profiles)
            row = previous.base["execute"](args, directory, OUT / "inputs" / case["stdin_sha256"], plan)
            previous.require(row["cpu_us"] > 0 and row["stdout_sha256"] == case["stdout_sha256"], "wrong result")
            if arm != "warmup":
                finish(arm, directory, row["launcher_pid"])
            receipts = {p.relative_to(OUT).as_posix(): previous.digest(p) for p in sorted(directory.rglob("*"))
                        if p.is_file()}
            row.update(sequence=index, **trial, receipts=receipts)
            progress.write(json.dumps(row) + "\n")
            progress.flush()
            rows.append(row)
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
