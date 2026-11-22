"""Replay canonical v11 records, terminal live clocks and CPU placement for this study."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import re
import runpy
import struct
import subprocess
import sys
import tarfile
import tempfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from cpp20_locality.export import parse_trace
from cpp20_locality.fixtures import manifest
from cpp20_locality.schedule import build

REPO = HERE.parents[1]
RESULT = HERE.parent / "results/gce-cpp20-locality"
ELF_ARCHIVE = "051251cd87b3119d1d2ddcbfd65e474f8294dc503efadf0871f783d8b5f18999"


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read_compressed(path):
    return json.loads(gzip.decompress(path.read_bytes()))


def release_reader(expected):
    current = REPO / "collect.py"
    if sha(current.read_bytes()) == expected:
        return runpy.run_path(str(current))["collect"]
    ref = "74d9188f9fd86c75f079277e6e5278b379a0a1d7:collect.py"
    source = subprocess.check_output(["git", "-C", REPO, "show", ref])
    require(sha(source) == expected, "canonical reader mismatch")
    with tempfile.TemporaryDirectory() as directory:
        frozen = Path(directory) / "collect.py"
        frozen.write_bytes(source)
        return runpy.run_path(str(frozen))["collect"]


def verify_counters(raw, expected, clock, arm, pid, budget, reader):
    with tempfile.TemporaryDirectory() as directory:
        (Path(directory) / f"process-{pid}.jsonl").write_bytes(raw)
        parsed = reader(Path(directory), pid)
    require(parsed == expected and parsed["valid"] is True and parsed["complete"] is True, "v11 replay mismatch")
    require(parsed["root_pid"] == pid and parsed["process_count"] == parsed["image_count"] == 1
            and parsed["segments"][0]["threads"] == 1 and parsed["processes"][0]["exit_code"] == 0
            and parsed["quality"] == "uninterrupted" and parsed["signals_observed"] == 0, "invalid process coverage")
    require(parsed["authority"] == "diagnostic_only" and parsed["integrity_verified"] is False
            and parsed["measurement_contract"]["trusted_verdict_eligible"] is False, "invalid counter authority")
    breakdown = parsed["rep_breakdown"]
    require(parsed["cpuid_profile"] == "x86-64-v2" and parsed["rep_telemetry_status"] == "movs_stos_only"
            and parsed["rep_bytes_enabled"] is True and parsed["unsupported_rep_entries"] == 0
            and breakdown["version"] == 1 and parsed["rep_bytes"] == breakdown["movs_bytes"] + breakdown["stos_bytes"]
            and parsed["instructions"] > 0, "invalid instruction or REP telemetry")
    diagnostic = arm.startswith("diag")
    require(("memory_operand_bytes" in parsed) == ("latency_bound" in parsed) == diagnostic, "wrong counter mode")
    if diagnostic:
        require(parsed["memory_operand_bytes"]["version"] == parsed["latency_bound"]["version"] == 1
                and parsed["memory_operand_bytes"]["bytes"] > 0
                and parsed["latency_bound"]["cycles_x16"] >= 4 * parsed["instructions"], "invalid diagnostic")
    else:
        words = (1, 0, parsed["instructions"], breakdown["movs_bytes"], breakdown["stos_bytes"], 0)
        require(clock == struct.pack("<6Q", *words) and sum(words[2:5]) <= budget, "terminal live clock mismatch")


def verify_elfs(path, cases):
    require(sha(path.read_bytes()) == ELF_ARCHIVE, "wrong Aonohako #154 ELF archive")
    programs = {case["program"]: case["binary_sha256"] for case in cases}
    with tarfile.open(path, "r:xz") as archive:
        for name, expected in programs.items():
            require(sha(archive.extractfile("bin/" + name).read()) == expected, "wrong executable: " + name)


def verify(result, elf_archive=None):
    protocol_bytes = (HERE / "protocol.json").read_bytes()
    plan = json.loads(protocol_bytes)
    tools = plan["tools_sha256"]
    cases = manifest()
    require(cases == json.loads((HERE / "cases.json").read_bytes()), "fixture oracle mismatch")
    for name in ("bench.py", "schedule.py", "guest.sh", "cases.json"):
        require(sha((HERE / name).read_bytes()) == tools[name], f"changed collector input {name}")
    require(build(cases) == plan["schedule"] and all(tools["bin/" + c["program"]] == c["binary_sha256"]
            for c in cases), "registration mismatch")
    if elf_archive:
        verify_elfs(elf_archive, cases)
    return verify_collection(result, protocol_bytes, cases, {"diag_near", "diag_span", "live_span"},
                             {"live", "live_span"})


def verify_collection(result, protocol_bytes, cases, sampled_arms, live_arms):
    """Replay every trial using the same CPU placement, wait4 and v11 record contract."""
    plan = json.loads(protocol_bytes)
    by_id = {case["case_id"]: case for case in cases}
    reader = release_reader(plan["tools_sha256"]["production_reader.py"])
    data = read_compressed(result / "dataset.json.gz")
    placement = read_compressed(result / "placement.json.gz")
    cleanup = json.loads((result / "cleanup.json").read_bytes())
    require(cleanup["instance_http"] == cleanup["boot_disk_http"] == 404 and cleanup["key_removed"] is True,
            "incomplete cleanup")
    require(data["schema"] == 1 and data["protocol_sha256"] == sha(protocol_bytes), "wrong dataset protocol")
    platform = data["platform"]
    require(platform["cpu"] == plan["cpu"] == 1 and platform["affinity"] == [1]
            and platform["cgroup_allowed_cpus"] == "1" and platform["cpu_platform"] == "AMD Milan"
            and platform["machine_type"] == "n2d-standard-4" and platform["threads_per_core"] == 1
            and platform["clock_tick_hz"] > 0 and platform["cpu_steal_ticks"] >= 0
            and platform["max_trace_overrun"] == platform["max_trace_dropped_events"] == 0, "unexpected platform")
    trace = (result / "exec-trace.txt").read_text()
    events, lines = parse_trace(trace)
    require(len(lines) == len(trace.splitlines()), "malformed scheduler trace")
    stats = (result / "trace-stats.txt").read_text()
    for field in ("overrun", "commit overrun", "dropped events"):
        require(list(map(int, re.findall(r"^" + field + r":\s*(\d+)", stats, re.M))) == [0, 0],
                "missing or lossy trace statistics: " + field)
    count = clocks = 0
    pids = set()
    consumed = set()
    with tarfile.open(result / "receipts.tar.xz", "r:xz") as archive:
        contents = {}
        for entry in archive:
            require(entry.isfile() and entry.name not in contents, "duplicate or non-file receipt")
            contents[entry.name] = archive.extractfile(entry).read()
        for i, (slot, row, observation) in enumerate(zip(plan["schedule"], data["rows"], placement, strict=True)):
            require({k: row[k] for k in slot} == slot and row["sequence"] == i, f"wrong trial at {i}")
            pid = row["launcher_pid"]
            require(type(pid) is int and pid > 0 and pid not in pids and row["exit_code"] == 0, f"invalid pid/exit at {i}")
            pids.add(pid)
            require(all(type(row[k]) is int and row[k] >= 0 for k in ("user_us", "sys_us", "cpu_us", "wall_us"))
                    and row["user_us"] + row["sys_us"] == row["cpu_us"] > 0 and row["wall_us"] > 0,
                    f"invalid wait4 accounting at {i}")
            arm = row["arm"]
            metered = arm not in ("warmup", "native")
            live = arm in live_arms
            case = by_id[row["case_id"]]
            executables = ["drrun", "libdynamorio.so"] if metered else [case["program"]]
            require(observation == dict(sequence=i, pid=pid, events=[dict(cpu=1, executable=s) for s in executables]),
                    f"wrong exec placement at {i}")
            require(events.get(pid) == observation["events"], f"trace mismatch at {i}")
            record = f"records/process-{pid}.jsonl"
            files = {"stdout", "stderr"}
            if metered:
                files.update(("production.json", record))
            if arm in sampled_arms:
                files.add("sample.json")
            if live:
                files.add("clock.bin")
            require(("clock_polls" in row) == live and (not live or
                    (type(row["clock_polls"]) is int and row["clock_polls"] > 0)), f"invalid live poll count at {i}")
            prefix = f"runs/{i:03d}/"
            require(set(row["receipts"]) == {prefix + f for f in files}, f"wrong receipts at {i}")
            content = {}
            for f in files:
                name = prefix + f
                raw = contents[name]
                require(sha(raw) == row["receipts"][name], "changed receipt: " + name)
                content[f] = raw
                consumed.add(name)
            require(content["stdout"] == case["stdout"].encode() and content["stderr"] == b""
                    and row["stdout_sha256"] == sha(content["stdout"]), f"wrong stdio at {i}")
            if metered:
                verify_counters(content[record], json.loads(content["production.json"]), content.get("clock.bin"),
                                arm, pid, plan["work_budget"], reader)
                count += 1
                clocks += live
        require(consumed == contents.keys(), "unexpected archive entries")
    require(events.keys() == pids, "unexpected traced PID")
    return len(pids), count, clocks


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result", type=Path, default=RESULT, nargs="?")
    parser.add_argument("--elf-archive", type=Path, help="original elfs.tar.xz from Aonohako #154")
    args = parser.parse_args()
    trials, records, clocks = verify(args.result, args.elf_archive)
    print(f"Verified {trials} CPU-pinned runs, {records} v11 records and {clocks} terminal work clocks.")
