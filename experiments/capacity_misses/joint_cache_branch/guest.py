"""Collect separate and simultaneous flat-cache/gshare diagnostics on the same CPU."""
import hashlib
import json
import os
from pathlib import Path
import resource
import shutil
import subprocess
import sys

from base_guest import OUT, ROOT, schedule, sha, trial


def predictor_trial(index, name, arm, case, flags):
    slot = ROOT / "slot"
    records = slot / "records"
    records.mkdir(parents=True)
    predictions = slot / "predictor.json"
    options = " ".join(["-outdir", str(records), "-cpuid_profile", "x86-64-v2", *flags])
    command = [ROOT / "dynamorio/bin64/drrun", "-root", ROOT / "dynamorio", "-quiet",
               "-client", ROOT / "probe.so", "00000000", options,
               "-client", ROOT / "branch.so", "00000001", f"-out {predictions} -bpred",
               "-follow_children", "-disable_traces", "-vm_size", "32M", "-vmheap_size", "32M",
               "--", ROOT / "bin" / name]
    with (ROOT / case["stdin"]).open("rb") as inp, (slot / "out").open("wb") as out, (slot / "err").open("wb") as err:
        with subprocess.Popen(command, stdin=inp, stdout=out, stderr=err,
                              env={"PATH":os.defpath, "LC_ALL":"C", "DYNAMORIO_CONFIGDIR":str(slot)}) as child:
            pid, status, usage = os.wait4(child.pid, 0)
            child.returncode = os.waitstatus_to_exitcode(status)
    output = sha(slot / "out")
    if child.returncode or output != case["expected_sha256"]:
        raise ValueError(f"incorrect exit or output: {arm}/{name}")
    (slot / "err").rename(OUT / f"probe-{index:03d}.txt")
    records.rename(OUT / f"records-{index:03d}")
    predictions.rename(OUT / f"branch-{index:03d}.json")
    shutil.rmtree(slot)
    return dict(index=index, case=name, arm=arm, pid=pid, exit_code=child.returncode,
                stdout_sha256=output, user_us=round(usage.ru_utime*1e6), system_us=round(usage.ru_stime*1e6))


def cpu_ticks():
    return next(line for line in Path("/proc/stat").read_text().splitlines() if line.startswith("cpu1 "))


def main(phase):
    plan = json.loads((ROOT / "plan.json").read_bytes())
    protocol = plan["protocol"]
    if phase not in protocol["phases"] or os.sched_getaffinity(0) != {protocol["cpu"]}:
        raise ValueError("incorrect guest phase or CPU")
    for name, case in plan["cases"].items():
        if sha(ROOT / "bin" / name) != case["elf_sha256"] or sha(ROOT / case["stdin"]) != case["stdin_sha256"]:
            raise ValueError("different input or program")
    for name, expected in plan["clients"].items():
        if sha(ROOT / name) != expected:
            raise ValueError("different diagnostic client")
    hard = resource.getrlimit(resource.RLIMIT_STACK)[1]
    resource.setrlimit(resource.RLIMIT_STACK, (hard, hard))
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    host = dict(phase=phase, cpus=[protocol["cpu"]], boot_sha256=hashlib.sha256(boot.encode()).hexdigest(),
                before=cpu_ticks(), plan_sha256=sha(ROOT / "plan.json"))
    with (OUT / "trials.jsonl").open("x") as stream:
        for index, (arm, name) in enumerate(schedule(plan, phase)):
            client = protocol["clients"][arm]
            shutil.copyfile(ROOT / client, ROOT / "probe.so")
            digest = sha(ROOT / "probe.so")
            if digest != plan["clients"][client]:
                raise ValueError("different installed client")
            run = predictor_trial if arm in ("joint", "branch") else trial
            row = run(index, name, arm, plan["cases"][name], protocol["modes"][arm])
            row["client_sha256"] = digest
            stream.write(json.dumps(row) + "\n")
            stream.flush()
    host["after"] = cpu_ticks()
    (OUT / "platform.json").write_text(json.dumps(host) + "\n")


if __name__ == "__main__":
    main(sys.argv[1])
