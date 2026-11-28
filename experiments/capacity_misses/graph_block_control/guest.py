"""Install each registered client at the same path before starting the timed process."""
import hashlib
import json
import os
from pathlib import Path
import resource
import shutil
import sys

from base_guest import OUT, ROOT, schedule, sha, trial


def proc_stat():
    return next(line for line in Path("/proc/stat").read_text().splitlines() if line.startswith("cpu1 "))


def main(phase):
    plan = json.loads((ROOT / "plan.json").read_bytes())
    if phase not in ("primary", "repeat") or os.sched_getaffinity(0) != {1}:
        raise ValueError("wrong CPU or guest")
    for name, case in plan["cases"].items():
        if sha(ROOT / "bin" / name) != case["elf_sha256"] or sha(ROOT / case["stdin"]) != case["stdin_sha256"]:
            raise ValueError("different ELF or input")
    for name, digest in plan["clients"].items():
        if sha(ROOT / name) != digest:
            raise ValueError("different client " + name)
    limit = resource.getrlimit(resource.RLIMIT_STACK)[1]
    resource.setrlimit(resource.RLIMIT_STACK, (limit, limit))
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    platform = dict(phase=phase, cpus=[1], boot_sha256=hashlib.sha256(boot.encode()).hexdigest(),
                    before=proc_stat(), plan_sha256=sha(ROOT / "plan.json"))
    with (OUT / "trials.jsonl").open("x") as stream:
        for i, (arm, name) in enumerate(schedule(plan, phase)):
            selected = plan["protocol"]["clients"][arm]
            target = ROOT / "probe.so"
            shutil.copyfile(ROOT / selected, target)
            digest = sha(target)
            if digest != plan["clients"][selected]:
                raise ValueError("installed wrong client")
            row = trial(i, name, arm, plan["cases"][name], plan["protocol"]["modes"][arm])
            row["client_sha256"] = digest
            stream.write(json.dumps(row) + "\n")
            stream.flush()
    platform["after"] = proc_stat()
    (OUT / "platform.json").write_text(json.dumps(platform) + "\n")


if __name__ == "__main__":
    main(sys.argv[1])
