"""Collect paired native and corrected-cache observations inside the pinned guest."""
import hashlib
import json
import os
from pathlib import Path
import resource
import runpy
import shutil
import subprocess
import sys
import time

ROOT = Path.cwd()
OUT = ROOT / "output"
CAPACITY = ROOT / "experiments/capacity_misses"
sys.path.insert(0, str(CAPACITY))
study = runpy.run_path(str(CAPACITY / "collect.py"))
collect = runpy.run_path(str(ROOT / "collect.py"))["collect"]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def trial(name, spec, arm, index, plan):
    # Keep paths, environment and counter options identical on every repetition.
    slot = ROOT / "slot"
    slot.mkdir()
    records = slot / "records"
    records.mkdir()
    exe = ROOT / "bin" / name
    cmd = [exe]
    if arm == "probe":
        cmd = [ROOT / "dynamorio/bin64/drrun", "-root", ROOT / "dynamorio", "-quiet", "-disable_traces",
               "-c", ROOT / "probe.so", "-outdir", records, "-rep_bytes", "-latency_bound",
               "-cpuid_profile", plan["protocol"]["cpuid"], "-probe_l1d", "-probe_outer", "--", exe]
    with (ROOT / spec["stdin"]).open("rb") as inp, (slot / "stdout").open("wb") as out, (slot / "stderr").open("wb") as err:
        start = time.monotonic_ns()
        with subprocess.Popen(cmd, cwd=ROOT, stdin=inp, stdout=out, stderr=err,
                              env={"PATH": os.defpath, "LC_ALL": "C", "DYNAMORIO_CONFIGDIR": str(slot)}) as child:
            _, status, usage = os.wait4(child.pid, 0)
            child.returncode = os.waitstatus_to_exitcode(status)
        wall_us = (time.monotonic_ns() - start) // 1000
    assert child.returncode == 0 and digest(slot / "stdout") == spec["expected_sha256"], (name, arm, child.returncode)
    user = round(usage.ru_utime * 1e6)
    system = round(usage.ru_stime * 1e6)
    row = dict(case=name, arm=arm, index=index, pid=child.pid, exit_code=child.returncode,
               stdout_sha256=digest(slot / "stdout"), user_us=user, system_us=system, cpu_us=user+system, wall_us=wall_us)
    assert row["cpu_us"] > 0
    log = (slot / "stderr").read_bytes()
    if arm == "probe":
        r = collect(records, child.pid)
        assert r["valid"] and r["complete"] and r["quality"] == "uninterrupted" and r["cpuid_profile"] == plan["protocol"]["cpuid"]
        assert r["process_count"] == r["image_count"] == r["segments"][0]["threads"] == 1 and r["signals_observed"] == 0
        l1d, = study["PROBE_L1D"].finditer(log)
        outer, = study["PROBE_OUTER"].finditer(log)
        assert not log[:l1d.start()].strip() and not log[l1d.end():outer.start()].strip() and not log[outer.end():].strip()
        row["l1d"] = list(map(int, l1d.groups()))
        row["outer"] = list(map(int, outer.groups()))
        row["instructions"] = r["instructions"]
        row["rep_bytes"] = r["rep_bytes"]
        row["latency_bound_x16"] = r["latency_bound"]["cycles_x16"]
        records.rename(OUT / f"records-{index:03d}")
        (slot / "stderr").rename(OUT / f"probe-{index:03d}.txt")
    else:
        assert not log
    shutil.rmtree(slot)
    return row


def main():
    phase, = sys.argv[1:]
    plan = json.loads((ROOT / "plan.json").read_bytes())
    protocol = plan["protocol"]
    assert phase in ("primary", "repeat") and os.sched_getaffinity(0) == {protocol["cpu"]}
    for name, spec in plan["cases"].items():
        assert digest(ROOT / "bin" / name) == spec["elf_sha256"] and digest(ROOT / spec["stdin"]) == spec["stdin_sha256"]
    stack = resource.getrlimit(resource.RLIMIT_STACK)[1]
    resource.setrlimit(resource.RLIMIT_STACK, (stack, stack))
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    def cpu_stat():
        return next(s for s in Path("/proc/stat").read_text().splitlines() if s.startswith("cpu1 "))
    platform = dict(boot_sha256=hashlib.sha256(boot.encode()).hexdigest(), phase=phase,
                    cpus=sorted(os.sched_getaffinity(0)), cpu_before=cpu_stat(), plan_sha256=digest(ROOT / "plan.json"))
    cases = list(plan["cases"])
    if phase == "repeat":
        cases.reverse()
    index = 0
    with (OUT / "trials.jsonl").open("x") as out:
        for i, arm in enumerate(protocol["rounds"]):
            for j in range(len(cases)):
                name = cases[(j+i) % len(cases)]
                row = trial(name, plan["cases"][name], arm, index, plan)
                out.write(json.dumps(row) + "\n")
                out.flush()
                index += 1
    platform["cpu_after"] = cpu_stat()
    (OUT / "platform.json").write_text(json.dumps(platform) + "\n")


if __name__ == "__main__":
    main()
