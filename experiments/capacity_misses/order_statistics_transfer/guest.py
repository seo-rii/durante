"""Measure the native, full-cache and gshare arms on the same original guest and CPU."""
import hashlib
import json
import os
from pathlib import Path
import resource
import shutil
import subprocess
import sys

from base_guest import OUT, ROOT, schedule, sha, trial


def branch_trial(index, name, case):
    slot = ROOT / "slot"
    records = slot / "records"
    records.mkdir(parents=True)
    predictions = slot / "predictor.json"
    command = [ROOT / "dynamorio/bin64/drrun", "-root", ROOT / "dynamorio", "-quiet",
               "-client", ROOT / "probe.so", "00000000",
               f"-outdir {records} -rep_bytes -cpuid_profile x86-64-v2 -latency_bound",
               "-client", ROOT / "branch.so", "00000001", f"-out {predictions} -bpred",
               "-follow_children", "-disable_traces", "-vm_size", "32M", "-vmheap_size", "32M",
               "--", ROOT / "bin" / name]
    with (ROOT / case["stdin"]).open("rb") as inp, (slot / "out").open("wb") as out, (slot / "err").open("wb") as err:
        with subprocess.Popen(command, stdin=inp, stdout=out, stderr=err,
                              env={"PATH":os.defpath,"LC_ALL":"C","DYNAMORIO_CONFIGDIR":str(slot)}) as child:
            pid, status, usage = os.wait4(child.pid, 0)
            child.returncode = os.waitstatus_to_exitcode(status)
    if child.returncode or (slot/"err").stat().st_size or sha(slot/"out") != case["expected_sha256"]:
        raise ValueError("failed branch trial " + name)
    (slot/"err").rename(OUT / f"probe-{index:03d}.txt")
    records.rename(OUT / f"records-{index:03d}")
    predictions.rename(OUT / f"branch-{index:03d}.json")
    digest = sha(slot/"out")
    shutil.rmtree(slot)
    return dict(index=index, case=name, arm="branch", pid=pid, exit_code=child.returncode, stdout_sha256=digest,
                user_us=round(usage.ru_utime*1e6), system_us=round(usage.ru_stime*1e6))


def main(phase):
    plan = json.loads((ROOT/"plan.json").read_bytes())
    if phase not in ("primary", "repeat") or os.sched_getaffinity(0) != {1}:
        raise ValueError("wrong phase or CPU")
    for name,c in plan["cases"].items():
        if sha(ROOT/"bin"/name) != c["elf_sha256"] or sha(ROOT/c["stdin"]) != c["stdin_sha256"]:
            raise ValueError("different input or executable")
    for name,expected in plan["clients"].items():
        if sha(ROOT/name) != expected:
            raise ValueError("wrong registered diagnostic")
    hard = resource.getrlimit(resource.RLIMIT_STACK)[1]
    resource.setrlimit(resource.RLIMIT_STACK, (hard,hard))
    def ticks():
        return next(r for r in Path("/proc/stat").read_text().splitlines() if r.startswith("cpu1 "))
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    host = dict(phase=phase,cpus=[1],boot_sha256=hashlib.sha256(boot.encode()).hexdigest(),
                before=ticks(),plan_sha256=sha(ROOT/"plan.json"))
    with (OUT/"trials.jsonl").open("x") as stream:
        for i,(arm,name) in enumerate(schedule(plan,phase)):
            filename = plan["protocol"]["clients"][arm]
            shutil.copyfile(ROOT/filename, ROOT/"probe.so")
            digest = sha(ROOT/"probe.so")
            if digest != plan["clients"][filename]:
                raise ValueError("installed client changed")
            case = plan["cases"][name]
            row = (branch_trial(i,name,case) if arm == "branch" else
                   trial(i,name,arm,case,plan["protocol"]["modes"][arm]))
            row["client_sha256"] = digest
            stream.write(json.dumps(row) + "\n")
            stream.flush()
    host["after"] = ticks()
    (OUT/"platform.json").write_text(json.dumps(host)+"\n")


if __name__ == "__main__":
    main(sys.argv[1])
