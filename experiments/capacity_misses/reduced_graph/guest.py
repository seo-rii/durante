"""Run all native/full/five-feature cases with identical working paths on the pinned guest."""
import hashlib
import json
import os
from pathlib import Path
import resource
import runpy
import shutil
import subprocess
import sys

from counters import counters

ROOT = Path.cwd()
OUT = ROOT / "output"
sys.path.insert(0, str(ROOT / "experiments/capacity_misses"))
capacity = runpy.run_path(str(ROOT / "experiments/capacity_misses/collect.py"))
collect = runpy.run_path(str(ROOT / "collect.py"))["collect"]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def schedule(plan, phase):
    names = list(plan["cases"])
    rounds = plan["protocol"]["rounds"]
    if phase == "repeat":
        names.reverse()
        rounds = [{"full":"reduced", "reduced":"full"}.get(a, a) for a in rounds]
    for index, arm in enumerate(rounds):
        for j in range(len(names)):
            yield arm, names[(index + j) % len(names)]


def trial(index, name, arm, case):
    slot = ROOT / "slot"
    records = slot / "records"
    records.mkdir(parents=True)
    argv = [ROOT / "bin" / name]
    if arm != "native":
        # Give drrun and the application the very same argv, config and paths for both clients.
        shutil.copyfile(ROOT / (arm + ".so"), slot / "probe.so")
        argv = [ROOT / "dynamorio/bin64/drrun", "-root", ROOT / "dynamorio", "-quiet", "-disable_traces", "-c",
                slot / "probe.so", "-outdir", records, "-rep_bytes", "-latency_bound", "-cpuid_profile", "x86-64-v2",
                "-probe_l1d", "-probe_outer", "--", *argv]
    with (ROOT / case["stdin"]).open("rb") as inp, (slot / "out").open("wb") as out, (slot / "err").open("wb") as err:
        with subprocess.Popen(argv, cwd=ROOT, stdin=inp, stdout=out, stderr=err,
                              env={"PATH":os.defpath, "LC_ALL":"C", "DYNAMORIO_CONFIGDIR":str(slot)}) as child:
            pid, status, usage = os.wait4(child.pid, 0)
            child.returncode = os.waitstatus_to_exitcode(status)
    if child.returncode or sha(slot / "out") != case["expected_sha256"]:
        raise ValueError(f"incorrect output or exit: {arm}/{name}")
    row = dict(index=index, case=name, arm=arm, pid=pid, exit_code=child.returncode, stdout_sha256=sha(slot / "out"),
               user_us=round(usage.ru_utime * 1e6), system_us=round(usage.ru_stime * 1e6))
    if arm == "native":
        if (slot / "err").stat().st_size:
            raise ValueError("unexpected native stderr")
    else:
        row["features"], row["skipped"] = counters(arm, collect(records, pid), (slot / "err").read_bytes(), capacity)
        records.rename(OUT / f"records-{index:03d}")
        (slot / "err").rename(OUT / f"probe-{index:03d}.txt")
    shutil.rmtree(slot)
    return row


def main():
    phase, = sys.argv[1:]
    plan = json.loads((ROOT / "plan.json").read_bytes())
    if phase not in ("primary", "repeat") or os.sched_getaffinity(0) != {1}:
        raise ValueError("unexpected phase or CPU")
    for name, case in plan["cases"].items():
        if sha(ROOT / "bin" / name) != case["elf_sha256"] or sha(ROOT / case["stdin"]) != case["stdin_sha256"]:
            raise ValueError("changed submission/input")
    for arm, digest in plan["clients"].items():
        if sha(ROOT / (arm + ".so")) != digest:
            raise ValueError("changed client " + arm)
    hard = resource.getrlimit(resource.RLIMIT_STACK)[1]
    resource.setrlimit(resource.RLIMIT_STACK, (hard, hard))
    def ticks():
        return next(r for r in Path("/proc/stat").read_text().splitlines() if r.startswith("cpu1 "))
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    host = dict(phase=phase, cpus=[1], boot_sha256=hashlib.sha256(boot.encode()).hexdigest(),
                before=ticks(), plan_sha256=sha(ROOT / "plan.json"))
    with (OUT / "trials.jsonl").open("x") as dest:
        for i, (arm, name) in enumerate(schedule(plan, phase)):
            dest.write(json.dumps(trial(i, name, arm, plan["cases"][name])) + "\n")
            dest.flush()
    host["after"] = ticks()
    (OUT / "platform.json").write_text(json.dumps(host) + "\n")


if __name__ == "__main__":
    main()
