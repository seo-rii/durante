"""Measure the same staged diagnostic on three original graph executables."""
import hashlib
import json
import os
from pathlib import Path
import resource
import shutil
import subprocess
import sys

ROOT = Path.cwd()
OUT = ROOT / "output"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def schedule(plan, phase):
    names = list(plan["cases"])
    sequence = [(arm, names[(i + j) % len(names)]) for i, arm in enumerate(plan["protocol"]["rounds"])
                for j in range(len(names))]
    return reversed(sequence) if phase == "repeat" else sequence


def trial(index, name, arm, case, flags):
    slot = ROOT / "slot"
    records = slot / "records"
    records.mkdir(parents=True)
    argv = [ROOT / "bin" / name]
    if arm != "native":
        argv = [ROOT / "dynamorio/bin64/drrun", "-root", ROOT / "dynamorio", "-quiet", "-disable_traces", "-c",
                ROOT / "probe.so", "-outdir", records, "-cpuid_profile", "x86-64-v2", *flags, "--", *argv]
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
    if sha(ROOT / "probe.so") != plan["client_sha256"]:
        raise ValueError("different full probe")
    hard = resource.getrlimit(resource.RLIMIT_STACK)[1]
    resource.setrlimit(resource.RLIMIT_STACK, (hard, hard))
    def ticks():
        return next(r for r in Path("/proc/stat").read_text().splitlines() if r.startswith("cpu1 "))
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    host = dict(phase=phase, cpus=[1], boot_sha256=hashlib.sha256(boot.encode()).hexdigest(),
                before=ticks(), plan_sha256=sha(ROOT / "plan.json"))
    with (OUT / "trials.jsonl").open("x") as dest:
        for i, (arm, name) in enumerate(schedule(plan, phase)):
            dest.write(json.dumps(trial(i, name, arm, plan["cases"][name], plan["protocol"]["modes"][arm])) + "\n")
            dest.flush()
    host["after"] = ticks()
    (OUT / "platform.json").write_text(json.dumps(host) + "\n")


if __name__ == "__main__":
    main()
