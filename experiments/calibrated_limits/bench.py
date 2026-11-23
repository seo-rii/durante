"""Collect per-ELF first captures, independent native times, and actual calibrated limit decisions."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import runpy
import subprocess
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from cpp20_locality.verify import require, sha
from guarded_native import anchor
from guarded_sampler import run as guarded


def collect(payload, binaries, out):
    raw = (HERE / "plan.json").read_bytes()
    plan = json.loads(raw)
    cases = plan["cases"]
    first = [c for c in cases if c["split"] == "calibration"]
    later = [c for c in cases if c["split"] == "holdout"]
    later.sort(key=lambda c: hashlib.sha256(c["case_id"].encode()).digest())
    affinity = sorted(os.sched_getaffinity(0))
    cgroup = Path("/proc/self/cgroup").read_text().strip().split(":")[-1].lstrip("/")
    allowed = (Path("/sys/fs/cgroup") / cgroup / "cpuset.cpus.effective").read_text().strip()
    require(affinity == [1] and allowed == "1", "expected guest cpuset and affinity CPU 1")
    out.mkdir(mode=0o700)
    for folder in ("inputs", "first", "policies", "native", "live"):
        (out / folder).mkdir()
    sys.path.insert(0, str(payload))
    base = runpy.run_path(str(payload / "base/bench.py"))
    start = base["steal"](1)
    for c in cases:
        (out / "inputs" / sha(c["stdin"].encode())).write_bytes(c["stdin"].encode())
        require(anchor.elf_digest(binaries / c["program"]) == c["binary_sha256"], "different executable")
    def paths(c):
        return binaries / c["program"], out / "inputs" / sha(c["stdin"].encode()), out / "policies" / (c["program"] + ".json")

    calibrations, native, runs = [], [], []
    for case in first:
        name = case["program"]
        exe, stdin, policy = paths(case)
        run = out / "first" / name
        metered = guarded.run(payload, exe, stdin, out / "profiles", run)
        require(metered["launches"][0]["stdout_sha256"] == sha(case["stdout"].encode()), "calibration output")
        anchor.calibrate(exe, stdin, run, policy)
        calibrations.append({"case_id": case["case_id"], "program": name})

    for round_id in range(plan["native_rounds"]):
        index = round_id % len(later)
        for case in later[index:] + later[:index]:
            seq = len(native)
            directory = out / "native" / f"{seq:03d}"
            directory.mkdir()
            exe, stdin, _ = paths(case)
            row = base["execute"]([exe], directory, stdin, {"trial_timeout_s": plan["timeout_ms"] // 1000})
            require(row["stdout_sha256"] == sha(case["stdout"].encode()) and row["cpu_us"] > 0, "bad native execution")
            native.append(dict(sequence=seq, case_id=case["case_id"], round=round_id + 1,
                               pid=row["launcher_pid"], user_us=row["user_us"], sys_us=row["sys_us"],
                               cpu_ns=row["cpu_us"] * 1000, stdout_sha256=row["stdout_sha256"]))

    jobs = [(c, "control", plan["control_limit_ms"]) for c in later]
    jobs += [(c, "limited", limit) for c in later for limit in c["limits_ms"]]
    jobs.sort(key=lambda job: hashlib.sha256(f"{job[0]['case_id']} {job[1]} {job[2]}".encode()).digest())
    for case, kind, limit in jobs:
        seq = len(runs)
        exe, stdin, policy = paths(case)
        directory = out / "live" / f"{seq:03d}"
        command = [sys.executable, "-B", str(HERE.parent / "guarded_native/live.py"),
                   "--payload", payload, "--policy", policy, "--elf", exe, "--stdin", stdin, "--out", directory,
                   "--limit-ms", str(limit), "--timeout-ms", str(plan["timeout_ms"]), "--poll-ms", str(plan["poll_ms"])]
        attempt = subprocess.run(command, capture_output=True, timeout=plan["timeout_ms"] / 1000 + 30)
        result = json.loads((directory / "result.json").read_bytes())
        exit_code = attempt.returncode
        require(exit_code in (0, 123) and not attempt.stderr and json.loads(attempt.stdout) == result,
                f"uncertified live result for {case['case_id']}: {result}")
        if result["observation"] == "terminal":
            require(result["stdout_sha256"] == sha(case["stdout"].encode()), "bad terminal output")
        require(kind != "control" or exit_code == 0, "control stopped before completion")
        runs.append(dict(sequence=seq, case_id=case["case_id"], kind=kind, limit_ms=limit, exit_code=exit_code))
    summary = dict(schema=1, plan_sha256=sha(raw), affinity=affinity, cgroup_cpus=allowed,
                   kernel=platform.release(), steal_delta=base["steal"](1) - start,
                   calibrations=calibrations, native=native, runs=runs)
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("payload", "binaries", "out"):
        parser.add_argument("--" + key, type=Path, required=True)
    args = parser.parse_args()
    collect(args.payload.resolve(), args.binaries.resolve(), args.out.resolve())
