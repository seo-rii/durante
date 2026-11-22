"""Run the frozen live-clock cost controls on the same guest path as the span study."""
import json
import os
from pathlib import Path
import platform
import runpy
import signal

from production_reader import collect

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "output"
base = runpy.run_path(str(ROOT / "base/bench.py"))
require, digest, quoted, save = (base[name] for name in ("require", "digest", "quoted", "save"))
NATIVE = ("native", "warmup")
SIDECARS = ("empty", "gate", "full", "fused")


def command(arm, program, directory):
    if arm in NATIVE:
        return [program]
    require(arm == "live" or arm in SIDECARS, "unknown live-clock arm")
    (directory / "records").mkdir()
    with (directory / "clock.bin").open("xb") as stream:
        stream.write(bytes(base["CLK"].size))
    options = ("-outdir " + quoted(directory / "records") + " -rep_bytes -cpuid_profile x86-64-v2"
               + " -live_work_v1 " + quoted(directory / "clock.bin"))
    primary = ROOT / "v11.so"
    sample_path = quoted(directory / "sample.json")
    if arm == "fused":
        primary = ROOT / "fused.so"
        options += " -span_report " + sample_path
    args = [ROOT / "dynamorio/bin64/drrun", "-quiet", "-client", primary, "00000000", options]
    if arm in ("empty", "gate", "full"):
        secondary = "span.so" if arm == "full" else arm + ".so"
        args.extend(["-client", ROOT / secondary, "00000001", "-out " + sample_path])
    return args + ["-follow_children", "-disable_traces", "-vm_size", "32M", "-vmheap_size", "32M", "--", program]


def finish_live(arm, directory, pid):
    record = collect(directory / "records", pid)
    require(record["complete"] and record["valid"] and record["quality"] == "uninterrupted"
            and record["signals_observed"] == 0 and record["process_count"] == record["image_count"] == 1
            and record["segments"][0]["threads"] == 1 and record["processes"][0]["exit_code"] == 0
            and record["root_pid"] == pid, "incomplete v11 work")
    require(record["authority"] == "diagnostic_only" and record["integrity_verified"] is False
            and record["measurement_contract"]["trusted_verdict_eligible"] is False
            and record["rep_bytes_enabled"] and record["cpuid_profile"] == "x86-64-v2"
            and record["rep_telemetry_status"] == "movs_stos_only" and record["unsupported_rep_entries"] == 0
            and record["instructions"] > 0 and "latency_bound" not in record
            and "memory_operand_bytes" not in record, "incorrect live mode")
    rep = record["rep_breakdown"]
    require(rep["version"] == 1 and record["rep_bytes"] == rep["movs_bytes"] + rep["stos_bytes"], "REP mismatch")
    require(base["CLK"].unpack((directory / "clock.bin").read_bytes()) ==
            (1, 0, record["instructions"], rep["movs_bytes"], rep["stos_bytes"], 0), "terminal clock mismatch")
    save(directory / "production.json", record)
    if arm in SIDECARS:
        sample = json.loads((directory / "sample.json").read_bytes())
        require(sample["pid"] == pid and sample["threads"] == 1, "wrong sampler owner")
        if arm in ("empty", "gate"):
            require(sample["schema"] == 1 and sample["arm"] == arm, "wrong control mode")
        else:
            require(sample["schema"] == 2 and sample["line_bytes"] == 64, "wrong span mode")
        if arm != "empty":
            require(sample["period"] == 1024, "wrong sampling period")


def main():
    plan = json.loads((ROOT / "protocol.json").read_bytes())
    for name, expected in plan["tools_sha256"].items():
        require(digest(ROOT / name) == expected, "changed frozen payload: " + name)
    cpu = plan["cpu"]
    require(os.sched_getaffinity(0) == {cpu}, "unexpected affinity")
    group = Path("/sys/fs/cgroup") / Path("/proc/self/cgroup").read_text().strip().split(":")[-1].lstrip("/")
    effective = (group / "cpuset.cpus.effective").read_text().strip()
    require(effective == str(cpu), "unexpected cgroup")
    OUT.mkdir()
    (OUT / "runs").mkdir()
    (OUT / "inputs").mkdir()
    cases = {c["case_id"]: c for c in json.loads((ROOT / "cases.json").read_bytes())}
    for c in cases.values():
        stdin = OUT / "inputs" / c["stdin_sha256"]
        stdin.write_bytes(c["stdin"].encode())
        require(digest(stdin) == c["stdin_sha256"] and digest(ROOT / "bin" / c["program"]) == c["binary_sha256"],
                "fixture mismatch")
    start = base["steal"](cpu)
    rows = []
    with (OUT / "progress.jsonl").open("x") as progress:
        for index, item in enumerate(plan["schedule"]):
            directory = OUT / "runs" / f"{index:03d}"
            directory.mkdir()
            arm, case = item["arm"], cases[item["case_id"]]
            cmd = command(arm, ROOT / "bin" / case["program"], directory)
            row = base["execute"](cmd, directory, OUT / "inputs" / case["stdin_sha256"], plan)
            require(row["stdout_sha256"] == case["stdout_sha256"] and row["cpu_us"] > 0, "bad stdout or rusage")
            if arm not in NATIVE:
                finish_live(arm, directory, row["launcher_pid"])
            receipts = {p.relative_to(OUT).as_posix(): digest(p) for p in sorted(directory.rglob("*")) if p.is_file()}
            row.update(sequence=index, **item, receipts=receipts)
            rows.append(row)
            progress.write(json.dumps(row) + "\n")
            progress.flush()
    model = next(line.split(":", 1)[1].strip() for line in Path("/proc/cpuinfo").read_text().splitlines()
                 if line.startswith("model name"))
    config = dict(cpu=cpu, affinity=sorted(os.sched_getaffinity(0)), cgroup_allowed_cpus=effective,
                  kernel=platform.release(), cpu_model=model, clock_tick_hz=os.sysconf("SC_CLK_TCK"),
                  cpu_steal_ticks=base["steal"](cpu) - start)
    save(OUT / "dataset.json", dict(schema=1, protocol_sha256=digest(ROOT / "protocol.json"), platform=config, rows=rows))


if __name__ == "__main__":
    signal.signal(signal.SIGALRM, base["timeout"])
    main()
