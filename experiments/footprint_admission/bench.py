"""Measure two chronological calibration banks with the same one-pass profiles and real deadlines."""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import platform
import subprocess
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from cpp20_locality.verify import require, sha
from first_run_sampler.profile import elf_digest
from shape_calibration import combined, select_policy
from shape_calibration.bench import native_rounds, pinned_cpu, write

METHODS = ("temporal", "footprint")


def limited(payload, binary, folder, stdin, policy, method, limit, out, plan):
    command = [sys.executable, "-B", HERE.parent / "shape_calibration/run.py",
               "--selection", folder / (method + "-selection.json"), "--run", folder,
               "--payload", payload, "--elf", binary, "--policy", policy, "--stdin", stdin,
               "--out", out, "--limit-ms", str(limit), "--poll-ms", str(plan["poll_ms"]),
               "--timeout-ms", str(plan["timeout_ms"])]
    proc = subprocess.run(command, capture_output=True, timeout=plan["timeout_ms"] / 1000 + 30)
    receipt = json.loads((out / "result.json").read_bytes())
    require(proc.returncode in (0, 123) and not proc.stderr and json.loads(proc.stdout) == receipt,
            "uncertified live limit: " + str(receipt.get("reason", proc.returncode)))
    return proc.returncode


def bench(payload, binaries, probe, out):
    raw = (HERE / "plan.json").read_bytes()
    rules = (HERE / "rules.json").read_bytes()
    plan = json.loads(raw)
    probe_sha = elf_digest(probe)
    require(probe_sha == json.loads(rules)["probe_sha256"], "unregistered probe")
    affinity, allowed = pinned_cpu()
    sys.path.insert(0, str(payload))
    import cost_bench as runtime
    timings = {"trial_timeout_s": plan["timeout_ms"] // 1000,
               "work_budget": 100_000_000_000, "poll_interval_ms": plan["poll_ms"]}
    out.mkdir(mode=0o700)
    for sub in ("inputs", "native", "cases"):
        (out / sub).mkdir()
    banks = defaultdict(lambda: {method: [] for method in METHODS})
    rows = []
    start = runtime.base["steal"](1)
    for seq, case in enumerate(plan["cases"]):
        key, name = case["case_id"], case["program"]
        binary = binaries / name
        require(elf_digest(binary) == case["binary_sha256"], "unregistered workload")
        stdin = out / "inputs" / sha(case["stdin"].encode())
        stdin.write_bytes(case["stdin"].encode())
        folder = out / "cases" / key
        folder.parent.mkdir(exist_ok=True)
        full, _ = combined.capture(payload, probe, binary, stdin, folder)
        require(full["stdout_sha256"] == sha(case["stdout"].encode()), "unexpected profile output")
        policies = {}
        for method, bank in banks[name].items():
            refs = [out / "cases" / prior for prior in bank]
            selection = select_policy.resolve(binary, folder, refs, probe_sha, method)
            write(folder / (method + "-selection.json"), selection)
            if selection["action"] == "calibrate":
                bank.append(key)
                selected = key
            else:
                selected = bank[selection["reference_index"]]
            policies[method] = dict(action=selection["action"], anchor_case=selected,
                                    prediction_ns=selection["prediction_ns"])
            (folder / "limits" / method).mkdir(parents=True)
        events = []
        for idx, ms in enumerate(case["limits_ms"]):
            order = METHODS if (seq + idx) % 2 == 0 else reversed(METHODS)
            for method in order:
                policy = out / "cases" / policies[method]["anchor_case"] / "policy.json"
                dest = folder / "limits" / method / f"{idx:02d}"
                code = limited(payload, binary, folder, stdin, policy, method, ms, dest, plan)
                events.append(dict(method=method, index=idx, limit_ms=ms, exit_code=code))
        rows.append(dict(sequence=seq, case_id=key, profile_pid=full["launcher_pid"],
                         profile_cpu_ns=full["cpu_us"] * 1000, policies=policies, limits=events))
    native = native_rounds(plan, binaries, out, runtime, timings)
    write(out / "summary.json", dict(schema=1, plan_sha256=sha(raw), rules_sha256=sha(rules),
          probe_sha256=probe_sha, affinity=affinity, cgroup_cpus=allowed, kernel=platform.release(),
          steal_delta=runtime.base["steal"](1) - start, rows=rows, native=native))


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    for name in ("payload", "binaries", "probe", "out"):
        cli.add_argument("--" + name, required=True, type=Path)
    args = cli.parse_args()
    bench(args.payload.resolve(), args.binaries.resolve(), args.probe.resolve(), args.out.resolve())
