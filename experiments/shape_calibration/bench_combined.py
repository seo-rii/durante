"""Pair separate and co-instrumented captures on the same pinned CPU and input."""
import argparse
import json
import os
from pathlib import Path
import platform
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from cpp20_locality.verify import require, sha
from first_run_sampler.profile import elf_digest
from shape_calibration import capture, combined
from shape_calibration.bench import write


def collect(payload, binaries, probe, out):
    raw = (HERE / "plan.json").read_bytes()
    study_raw = (HERE / "combined-plan.json").read_bytes()
    rules_raw = (HERE / "rules.json").read_bytes()
    plan = json.loads(study_raw)
    probe_sha = elf_digest(probe)
    require(plan["corpus_sha256"] == sha(raw) and plan["rules_sha256"] == sha(rules_raw)
            and probe_sha == json.loads(rules_raw)["probe_sha256"], "capture inputs differ from registration")
    affinity = sorted(os.sched_getaffinity(0))
    cgroup = Path("/proc/self/cgroup").read_text().strip().split(":")[-1].lstrip("/")
    allowed = (Path("/sys/fs/cgroup") / cgroup / "cpuset.cpus.effective").read_text().strip()
    require(affinity == [1] and allowed == "1", "expected CPU 1 and its cgroup")
    sys.path.insert(0, str(payload))
    import cost_bench as runtime
    cases = sorted(json.loads(raw)["cases"], key=lambda c: sha(c["case_id"].encode()))
    out.mkdir(mode=0o700)
    (out / "runs").mkdir()
    (out / "inputs").mkdir()
    for c in cases:
        (out / "inputs" / sha(c["stdin"].encode())).write_bytes(c["stdin"].encode())
        require(elf_digest(binaries / c["program"]) == c["binary_sha256"], "workload ELF changed")
    rows = []
    steal = runtime.base["steal"](1)
    for repeat in range(plan["repetitions"]):
        for pos in range(len(cases)):
            index = (repeat + pos) % len(cases)
            c = cases[index]
            seq = len(rows)
            base = out / "runs" / f"{seq:03d}"
            base.mkdir()
            order = ["separate", "combined"] if (repeat + index) % 2 == 0 else ["combined", "separate"]
            arms = {}
            for arm in order:
                fn = combined.capture if arm == "combined" else capture.capture
                row, extra = fn(payload, probe, binaries / c["program"],
                                out / "inputs" / sha(c["stdin"].encode()), base / arm)
                require(extra["stdout_sha256"] == row["stdout_sha256"] == sha(c["stdout"].encode())
                        and (extra["pid"] == row["launcher_pid"]) == (arm == "combined"), "output or launch mismatch")
                full_ns = row["cpu_us"] * 1000
                arms[arm] = dict(profile_pid=row["launcher_pid"], temporal_pid=extra["pid"],
                                 profile_cpu_ns=full_ns, temporal_cpu_ns=extra["cpu_ns"],
                                 target_cpu_ns=full_ns if arm == "combined" else full_ns + extra["cpu_ns"])
            rows.append(dict(sequence=seq, round=repeat+1, case_id=c["case_id"], order=order, arms=arms))
    write(out / "summary.json", dict(schema=1, plan_sha256=sha(raw), combined_plan_sha256=sha(study_raw),
          rules_sha256=sha(rules_raw), probe_sha256=probe_sha, affinity=affinity, cgroup_cpus=allowed,
          kernel=platform.release(), steal_delta=runtime.base["steal"](1)-steal, rows=rows))


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    for name in ("payload", "probe", "binaries", "out"):
        cli.add_argument("--" + name, required=True, type=Path)
    args = cli.parse_args()
    collect(args.payload.resolve(), args.binaries.resolve(), args.probe.resolve(), args.out.resolve())
