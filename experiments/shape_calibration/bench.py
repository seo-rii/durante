"""Collect chronological input-shape calibrations, real enforced deadlines and independent pinned native references."""
import argparse
from collections import defaultdict
import json
import os
from pathlib import Path
import platform
import subprocess
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from cpp20_locality.verify import require, sha
from first_run_sampler.profile import elf_digest
from shape_calibration import capture, select_policy


def write(path, data):
    path.write_text(json.dumps(data, indent=2) + "\n")


def limited(payload, binary, policy, stdin, out, limit, plan):
    cli = HERE.parent / "guarded_native/live.py"
    args = [sys.executable, "-B", cli, "--payload", payload, "--elf", binary, "--policy", policy,
            "--stdin", stdin, "--out", out, "--limit-ms", str(limit),
            "--poll-ms", str(plan["poll_ms"]), "--timeout-ms", str(plan["timeout_ms"])]
    proc = subprocess.run(args, capture_output=True, timeout=plan["timeout_ms"] / 1000 + 30)
    receipt = json.loads((out / "result.json").read_bytes())
    require(proc.returncode in (0, 123) and not proc.stderr and json.loads(proc.stdout) == receipt,
            "uncertified live limit: " + str(receipt.get("reason", proc.returncode)))
    return proc.returncode


def pinned_cpu():
    affinity = sorted(os.sched_getaffinity(0))
    cgroup = Path("/proc/self/cgroup").read_text().strip().split(":")[-1].lstrip("/")
    allowed = (Path("/sys/fs/cgroup") / cgroup / "cpuset.cpus.effective").read_text().strip()
    require(affinity == [1] and allowed == "1", "expected CPU 1 and its cgroup")
    return affinity, allowed


def native_rounds(plan, binaries, out, runtime, timings):
    native = []
    held = sorted((c for c in plan["cases"] if c["split"] == "holdout"), key=lambda c: sha(c["case_id"].encode()))
    for round_id in range(plan["native_rounds"]):
        offset = round_id % len(held)
        for case in held[offset:] + held[:offset]:
            seq = len(native)
            directory = out / "native" / f"{seq:03d}"
            directory.mkdir()
            binary = binaries / case["program"]
            stdin = out / "inputs" / sha(case["stdin"].encode())
            row = runtime.base["execute"]([binary], directory, stdin, timings)
            require(row["stdout_sha256"] == sha(case["stdout"].encode()) and row["cpu_us"] > 0, "native output or CPU")
            native.append(dict(sequence=seq, case_id=case["case_id"], round=round_id+1, pid=row["launcher_pid"],
                               user_us=row["user_us"], sys_us=row["sys_us"], cpu_ns=row["cpu_us"]*1000,
                               stdout_sha256=row["stdout_sha256"]))
    return native


def bench(payload, binaries, probe, out):
    raw = (HERE / "plan.json").read_bytes()
    rules_raw = (HERE / "rules.json").read_bytes()
    plan, rules = json.loads(raw), json.loads(rules_raw)
    probe_sha = elf_digest(probe)
    require(probe_sha == rules["probe_sha256"], "unregistered temporal probe")
    affinity, allowed = pinned_cpu()
    sys.path.insert(0, str(payload))
    import cost_bench as runtime
    timings = {"trial_timeout_s": plan["timeout_ms"] // 1000,
               "work_budget": 100_000_000_000, "poll_interval_ms": plan["poll_ms"]}
    out.mkdir(mode=0o700)
    for sub in ("inputs", "native", "cases"):
        (out / sub).mkdir()
    banks = defaultdict(lambda: {"shape": [], "temporal": []})
    rows = []
    start = runtime.base["steal"](1)
    for i, case in enumerate(plan["cases"]):
        name, key = case["program"], case["case_id"]
        binary = binaries / name
        require(elf_digest(binary) == case["binary_sha256"], "unregistered workload binary")
        raw_input = case["stdin"].encode()
        stdin = out / "inputs" / sha(raw_input)
        stdin.write_bytes(raw_input)
        folder = out / "cases" / key
        folder.parent.mkdir(exist_ok=True)
        spatial, temporal = capture.capture(payload, probe, binary, stdin, folder)
        require(spatial["stdout_sha256"] == sha(case["stdout"].encode()), "wrong spatial output")
        policies, decisions = {}, {}
        for method, bank in banks[name].items():
            refs = [out / "cases" / old for old in bank]
            result = select_policy.resolve(binary, folder, refs, probe_sha, method)
            selected = bank[result["reference_index"]] if result["action"] == "reuse" else key
            if selected == key:
                bank.append(key)
            decisions[method] = result
            policies[method] = dict(action=result["action"], anchor_case=selected, prediction_ns=result["prediction_ns"])
        write(folder / "selection.json", decisions)
        events = []
        selected = out / "cases" / policies["temporal"]["anchor_case"] / "policy.json"
        (folder / "live").mkdir()
        for j, deadline in enumerate(case["limits_ms"]):
            dest = folder / "live" / f"{j:02d}"
            code = limited(payload, binary, selected, stdin, dest, deadline, plan)
            events.append(dict(index=j, limit_ms=deadline, exit_code=code))
        plain = folder / "plain"
        plain.mkdir()
        cmd = runtime.command("live", binary, plain)
        row = runtime.base["execute"](cmd, plain, stdin, timings)
        runtime.finish_live("live", plain, row["launcher_pid"])
        require(row["stdout_sha256"] == spatial["stdout_sha256"], "wrong plain v11 output")
        write(plain / "result.json", row)
        rows.append(dict(sequence=i, case_id=key, profile_pid=spatial["launcher_pid"],
                         profile_cpu_ns=spatial["cpu_us"] * 1000, temporal_pid=temporal["pid"],
                         temporal_cpu_ns=temporal["cpu_ns"], plain_pid=row["launcher_pid"],
                         plain_cpu_ns=row["cpu_us"] * 1000, policies=policies, live=events))

    native = native_rounds(plan, binaries, out, runtime, timings)
    write(out / "summary.json", dict(schema=1, plan_sha256=sha(raw), rules_sha256=sha(rules_raw),
          probe_sha256=probe_sha, affinity=affinity, cgroup_cpus=allowed, kernel=platform.release(),
          steal_delta=runtime.base["steal"](1)-start, rows=rows, native=native))


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    for name in ("payload", "binaries", "probe", "out"):
        cli.add_argument("--"+name, type=Path, required=True)
    args = cli.parse_args()
    bench(args.payload.resolve(), args.binaries.resolve(), args.probe.resolve(), args.out.resolve())
