"""Measure guarded sampling, then execute the registered full diagnostic fallback when needed."""
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import time

import first_bench as first
from guard import decision, read_report
from profile import capture_for_digest, resolve_for_digest
from schedule import effective_schedule

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "output"
previous = first.previous
require = previous.require


def guarded(item, index, case, profiles, plan):
    directory = OUT / "runs" / f"{index:03d}"
    directory.mkdir()
    program = ROOT / "bin" / case["program"]
    stdin = OUT / "inputs" / case["stdin_sha256"]
    wall_start = time.monotonic_ns()
    parent_start = time.process_time_ns()
    hash_start = time.process_time_ns()
    elf = previous.digest(program)
    require(elf == case["binary_sha256"], "wrong guarded ELF")
    hash_cpu = time.process_time_ns() - hash_start
    cmd = previous.command("live", program, directory)
    lookup_start = time.process_time_ns()
    profile_path = profiles[elf]
    raw = profile_path.read_bytes()
    offsets = resolve_for_digest(json.loads(raw), elf)
    lookup_cpu = time.process_time_ns() - lookup_start
    sample_path, guard_path = directory / "sample.json", directory / "guard.json"
    opts = ("-out " + previous.quoted(sample_path) + " -sites " + offsets
            + " -guard " + previous.quoted(guard_path))
    pos = cmd.index("-follow_children")
    cmd[pos:pos] = ["-client", ROOT / "guarded.so", "00000001", opts]
    row = previous.base["execute"](cmd, directory, stdin, plan)
    # Match the first-run study's acquisition interval. Scientific aggregation
    # is outside this interval; neither result selection nor a rerun can proceed
    # until both the canonical v11 report and the terminal work clock validate.
    parent_cpu = time.process_time_ns() - parent_start
    elapsed = time.monotonic_ns() - wall_start
    require(row["stdout_sha256"] == case["stdout_sha256"] and row["cpu_us"] > 0, "incorrect guarded output")
    previous.finish_live("live", directory, row["launcher_pid"])
    decision_wall_start = time.monotonic_ns()
    decision_start = time.process_time_ns()
    result = decision(read_report(sample_path), read_report(guard_path), row["launcher_pid"])
    decision_cpu = time.process_time_ns() - decision_start
    parent_cpu += decision_cpu
    elapsed += time.monotonic_ns() - decision_wall_start
    require(0 < hash_cpu and 0 < lookup_cpu and 0 < decision_cpu
            and hash_cpu + lookup_cpu + decision_cpu <= parent_cpu, "invalid guarded accounting")
    receipts = {p.relative_to(OUT).as_posix(): previous.digest(p) for p in sorted(directory.rglob("*")) if p.is_file()}
    return dict(row, sequence=index, **item, receipts=receipts, elf_sha256=elf, hash_cpu_ns=hash_cpu,
                lookup_cpu_ns=lookup_cpu, build_cpu_ns=0, decision_cpu_ns=decision_cpu, parent_cpu_ns=parent_cpu,
                workflow_cpu_ns=row["cpu_us"] * 1000 + parent_cpu, workflow_wall_ns=elapsed,
                profile_path=profile_path.relative_to(OUT).as_posix(), profile_sha256=hashlib.sha256(raw).hexdigest(),
                decision=result)


def collect(item, index, case, profiles, plan):
    arm = item["arm"]
    if arm in ("guarded", "audit_guarded"):
        return guarded(item, index, case, profiles, plan)
    backend = "full" if arm in ("confirmation", "audit_fallback") else arm
    row = first.trial(dict(item, arm=backend), index, case, profiles, plan)
    row.update(arm=arm, decision_cpu_ns=0)
    if arm == "confirmation":
        directory = OUT / "runs" / f"{index:03d}"
        elf = row["elf_sha256"]
        observed = capture_for_digest(elf, case["stdin"].encode(), (directory / "sample.json").read_bytes(),
                                      row["launcher_pid"], arm="full")
        cache = profiles[elf]
        frozen = json.loads(cache.read_bytes())
        row.update(confirmation_offsets=observed["offsets"], confirmation_matches=observed["offsets"] == frozen["offsets"],
                   confirmation_profile_path=cache.relative_to(OUT).as_posix(),
                   confirmation_profile_sha256=previous.digest(cache))
    return row


def finish_recovery(audit, row, source):
    receipt = f"runs/{row['sequence']:03d}/sample.json"
    audit.update(final_source=source, selected_sequence=row["sequence"], final_receipt=receipt,
                 final_receipt_sha256=row["receipts"][receipt])


def main():
    plan = json.loads((ROOT / "protocol.json").read_bytes())
    for p, expected in plan["tools_sha256"].items():
        require(previous.digest(ROOT / p) == expected, "changed collector payload: " + p)
    cpu = plan["cpu"]
    group = Path("/sys/fs/cgroup") / Path("/proc/self/cgroup").read_text().strip().split(":")[-1].lstrip("/")
    allowed = (group / "cpuset.cpus.effective").read_text().strip()
    require(os.sched_getaffinity(0) == {cpu} and allowed == str(cpu), "incorrect CPU placement")
    cases = {c["case_id"]: c for c in json.loads((ROOT / "cases.json").read_bytes())}
    OUT.mkdir()
    (OUT / "runs").mkdir()
    (OUT / "inputs").mkdir()
    shutil.copytree(ROOT / "profiles", OUT / "profiles")
    profiles = {p.stem: p for p in (OUT / "profiles").glob("*.json")}
    require(len(profiles) == 6, "wrong frozen profile set")
    for c in cases.values():
        path = OUT / "inputs" / c["stdin_sha256"]
        path.write_bytes(c["stdin"].encode())
        require(previous.digest(path) == c["stdin_sha256"], "input mismatch")
    before = previous.base["steal"](cpu)
    rows, recovery = [], {}
    with (OUT / "progress.jsonl").open("x") as progress:
        for slot in plan["schedule"]:
            case_id = slot["case_id"]
            if slot.get("if_run_full") and recovery[case_id]["decision"]["status"] != "run_full":
                continue
            item = {k: v for k, v in slot.items() if k != "if_run_full"}
            row = collect(item, len(rows), cases[case_id], profiles, plan)
            rows.append(row)
            if item["arm"] == "audit_guarded":
                audit = recovery[case_id] = dict(guarded_sequence=row["sequence"], fallback_sequence=None,
                                                 decision=row["decision"])
                if row["decision"]["status"] == "use_profiled":
                    finish_recovery(audit, row, "guarded")
            elif item["arm"] == "audit_fallback":
                audit = recovery[case_id]
                audit["fallback_sequence"] = row["sequence"]
                finish_recovery(audit, row, "full")
            progress.write(json.dumps(row) + "\n")
            progress.flush()
    require(len(rows) == len(effective_schedule(plan, recovery)) and all("final_source" in a for a in recovery.values()),
            "incomplete audit")
    model = next(s.split(":", 1)[1].strip() for s in Path("/proc/cpuinfo").read_text().splitlines()
                 if s.startswith("model name"))
    platform_data = dict(cpu=cpu, affinity=sorted(os.sched_getaffinity(0)), cgroup_allowed_cpus=allowed,
                         kernel=platform.release(), cpu_model=model, clock_tick_hz=os.sysconf("SC_CLK_TCK"),
                         cpu_steal_ticks=previous.base["steal"](cpu) - before)
    previous.save(OUT / "dataset.json", dict(schema=1, protocol_sha256=previous.digest(ROOT / "protocol.json"),
                                             platform=platform_data, rows=rows, recovery=recovery))


if __name__ == "__main__":
    signal.signal(signal.SIGALRM, previous.base["timeout"])
    main()
