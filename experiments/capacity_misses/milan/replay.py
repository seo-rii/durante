"""Replay the pinned Milan contest observations and frozen relative clock predictions."""
import argparse
from collections import Counter, defaultdict
import hashlib
import io
import json
import math
from datetime import datetime
from pathlib import Path
import re
import runpy
import statistics
import sys
import tarfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE.parent))
from analyze_v2 import MODELS, features, fit, predict
from repeatability import probe_repeatable

collector = runpy.run_path(str(ROOT / "collect.py"))
capacity = runpy.run_path(str(HERE.parent / "collect.py"))
EXEC = re.compile(r"\[(\d+)\].*sched_process_exec: filename=(\S+) pid=(\d+) old_pid=(\d+)")


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def relative_verdict(submission, intended, factor):
    """Classify all cross-pairs of observed values using an inclusive deadline."""
    if max(submission) <= factor * min(intended):
        return "accept"
    if min(submission) > factor * max(intended):
        return "timeout"
    return "indeterminate"


def restored_counters(row, stderr, records, cpuid):
    pid = row["pid"]
    result = collector["aggregate"]({pid: collector["parse_records"](pid, records)}, pid)
    require(result["valid"] and result["complete"] and result["quality"] == "uninterrupted" and
            result["root_pid"] == pid and result["process_count"] == result["image_count"] == result["segments"][0]["threads"] == 1
            and result["rep_telemetry_status"] == "movs_stos_only" and result["cpuid_profile"] == cpuid
            and result["processes"][0]["exit_code"] == 0, "invalid process receipt")
    l1, = capacity["PROBE_L1D"].finditer(stderr)
    out, = capacity["PROBE_OUTER"].finditer(stderr)
    l1d, outer = list(map(int, l1.groups())), list(map(int, out.groups()))
    require(l1d == row["l1d"] and outer == row["outer"] and outer[:3] == [4096, 32768, 524288]
            and row["latency_bound_x16"] == result["latency_bound"]["cycles_x16"]
            and row["rep_bytes"] == result["rep_bytes"] and row["instructions"] == result["instructions"],
            "probe counters differ from recorded process or cache output")
    breakdown = result["rep_breakdown"]
    return dict(instructions=result["instructions"], movs_bytes=breakdown["movs_bytes"],
                stos_bytes=breakdown["stos_bytes"], latency_bound_x16=row["latency_bound_x16"],
                outer_misses=outer[4:7], outer_stream=outer[7:10], outer_dependent=outer[10:13],
                l1d_dependent=outer[13], l1d_misses=l1d[2], l1d=l1d, outer=outer)


def read_boot(path, phase, plan, reg, infra):
    raw = path.read_bytes()
    require(sha(raw) == infra["raw_sha256"] and infra["archive_sha256"] == reg["archive_sha256"], "archive digest")
    require(infra["platform"]["cpu_platform"] == "AMD Milan" and infra["platform"]["machine_type"] == "n2d-standard-4"
            and infra["platform"]["threads_per_core"] == 1 and infra["platform"]["disk_auto_delete"]
            and not infra["platform"]["external_ip"] and not infra["platform"]["service_account"]
            and infra["cleanup"]["instance_http"] == infra["cleanup"]["boot_disk_http"] == 404
            and infra["cleanup"]["key_removed"] and infra["exit"] == 0, "unexpected GCE lifecycle")
    protocol = plan["protocol"]
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
        def contents(name):
            return archive.extractfile("output/" + name).read()
        host = json.loads(contents("platform.json"))
        require(host["phase"] == phase and host["boot_sha256"] == infra["boot_sha256"] and host["cpus"] == [1]
                and host["plan_sha256"] == reg["payload"]["plan.json"], "guest registration")
        by_pid = defaultdict(list)
        for cpu, filename, pid, old in EXEC.findall(contents("exec-trace.txt").decode()):
            if pid == old:
                by_pid[int(pid)].append((int(cpu), filename))
        rows = [json.loads(line) for line in contents("trials.jsonl").splitlines()]
        order = list(plan["cases"])
        if phase == "repeat":
            order.reverse()
        schedule = [(arm, order[(i + j) % len(order)]) for i, arm in enumerate(protocol["rounds"])
                    for j in range(len(order))]
        require(len(rows) == len(schedule) == reg["expected_rows"]["."] and len({r["pid"] for r in rows}) == len(rows),
                "incomplete execution schedule")
        cases = defaultdict(lambda: dict(native=[], probe=[], probe_cpu=[]))
        for i, (r, (arm, name)) in enumerate(zip(rows, schedule, strict=True)):
            require(r["index"] == i and r["arm"] == arm and r["case"] == name and r["exit_code"] == 0
                    and r["stdout_sha256"] == plan["cases"][name]["expected_sha256"]
                    and 0 < r["cpu_us"] == r["user_us"] + r["system_us"] and r["wall_us"] > 0, "trial receipt")
            trace = by_pid[r["pid"]]
            app = "/var/tmp/durante-pr/bin/" + name
            expected = [app] if arm == "native" else ["/var/tmp/durante-pr/dynamorio/bin64/drrun",
                                                       "/var/tmp/durante-pr/dynamorio/lib64/release/libdynamorio.so"]
            require([f for cpu, f in trace] == expected and all(cpu == 1 for cpu, f in trace),
                    f"{phase}/{i}: scheduler trace {trace}")
            if arm == "native":
                cases[name]["native"].append(r["cpu_us"])
            else:
                pid = r["pid"]
                counters = restored_counters(r, contents(f"probe-{i:03d}.txt"),
                    contents(f"records-{i:03d}/process-{pid}.jsonl"), protocol["cpuid"])
                cases[name]["probe"].append(counters)
                cases[name]["probe_cpu"].append(r["cpu_us"])
    steal = int(host["cpu_after"].split()[8]) - int(host["cpu_before"].split()[8])
    require(steal >= 0, "negative CPU steal")
    return cases, steal


def classify(cases, plan):
    counted = {model: Counter() for model in plan["protocol"]["models"]}
    decisions = []
    for problem, (anchor, *variants) in plan["protocol"]["groups"].items():
        intended = cases[anchor]
        for name in variants:
            sample = cases[name]
            for factor in plan["protocol"]["factors"]:
                native = relative_verdict(sample["native"], intended["native"], factor)
                simulated = {}
                for model, weights in plan["weights"][problem].items():
                    estimates = [[predict(weights, features(p)) for p in c["probe"]] for c in (sample, intended)]
                    clock = relative_verdict(*estimates, factor)
                    if "indeterminate" in (native, clock):
                        outcome = "indeterminate"
                    elif clock == native:
                        outcome = "agree"
                    else:
                        outcome = "false_accept" if clock == "accept" else "false_timeout"
                    counted[model][outcome] += 1
                    simulated[model] = clock
                decisions.append(dict(case=name, factor=factor, native=native, clock=simulated))
    return decisions, counted


def replay():
    plan = json.loads((HERE / "frozen.json").read_bytes())
    reg = json.loads((HERE / "registration.json").read_bytes())
    files = reg.pop("files")
    reg["payload"] = {item["path"]: item["sha256"] for item in files}
    require(len(files) == len(reg["payload"]), "duplicate registered payload path")
    # Reconstruct the exact path-keyed JSON that was pushed before collection.
    original = {key: reg[key] for key in ("payload", "archive_sha256", "expected_rows")}
    require(sha((json.dumps(original, indent=2) + "\n").encode()) == reg["original_registration_sha256"],
            "different preregistration bytes")
    infra = json.loads((HERE / "evidence/cloud.json").read_bytes())
    require(sha((HERE / "frozen.json").read_bytes()) == reg["payload"]["plan.json"]
            and plan["protocol"] == json.loads((HERE / "protocol.json").read_bytes()), "changed plan")
    for field in ("boot_sha256", "instance_id", "boot_disk_id"):
        require(infra["primary"][field] != infra["repeat"][field], "reused GCE " + field)
    for facts in infra.values():
        require(facts["registration_commit"] == reg["freeze_commit"] == "eb28afa52558cd2772cf79a88e2136b812a94421"
                and datetime.fromisoformat(facts["started_at"]) < datetime.fromisoformat(facts["platform"]["created_at"])
                < datetime.fromisoformat(facts["cleanup"]["verified_at"]), "invalid GCE registration or chronology")
    for name, source in {"guest.py": HERE / "guest.py", "guest.sh": HERE / "guest.sh", "collect.py": ROOT / "collect.py",
                         "experiments/capacity_misses/collect.py": HERE.parent / "collect.py",
                         "experiments/capacity_misses/repeatability.py": HERE.parent / "repeatability.py",
                         "tests/contest/problems.py": ROOT / "tests/contest/problems.py"}.items():
        require(sha(source.read_bytes()) == reg["payload"][name], "registered source changed: " + name)
    archive = (HERE.parent / "results/v2/results.json").read_bytes()
    require(sha(archive) == plan["calibration_sha256"], "original training data changed")
    historical = json.loads(archive)
    for problem, names in plan["protocol"]["groups"].items():
        calibration = [(features(c["probe"][0]), statistics.median(c["native_cpu_s"]))
                       for n, c in historical["cases"].items()
                       if c["role"] == "calibration" and n != names[0]]
        require(len(calibration) == 35, "unexpected training set")
        for model, weights in plan["weights"][problem].items():
            rebuilt, _ = fit(calibration, MODELS[model])
            require(rebuilt.keys() == weights.keys() and
                    all(math.isclose(rebuilt[k], v, rel_tol=1e-8, abs_tol=1e-19) for k, v in weights.items()),
                    "published weights differ from original leave-one-problem-out fit")
        for name in names:
            spec = plan["cases"][name]
            old = historical["cases"][name]
            require(spec["stdin_sha256"] == old["stdin_sha256"] and spec["expected_sha256"] == old["expected_sha256"]
                    and spec["source_sha256"] == historical["built"][name]["source_sha256"]
                    and spec["elf_sha256"] == reg["payload"]["bin/" + name], "different study inputs")
    report = {}
    samples = {}
    for phase in ("primary", "repeat"):
        cases, steal = read_boot(HERE / "evidence" / (phase + ".tar.gz"), phase, plan, reg, infra[phase])
        samples[phase] = cases
        decisions, counted = classify(cases, plan)
        tables = {}
        for name, c in cases.items():
            times = c["native"]
            median = statistics.median(times)
            tables[name] = dict(min_us=min(times), median_us=median, max_us=max(times), native_samples=len(times),
                probe_samples=len(c["probe"]), exact_counters=probe_repeatable(c["probe"]),
                probe_over_native=round(statistics.median(c["probe_cpu"]) / median, 2))
        report[phase] = dict(cases=tables, decisions=decisions, outcomes=counted, steal_ticks=steal)
    report["exact_across_all_four"] = {name: probe_repeatable(samples["primary"][name]["probe"] +
        samples["repeat"][name]["probe"]) for name in plan["cases"]}
    combined = {n: {field: samples["primary"][n][field] + samples["repeat"][n][field]
                    for field in ("native", "probe")} for n in plan["cases"]}
    _, report["combined_outcomes"] = classify(combined, plan)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
    report = replay()
    if args.verify:
        require(json.loads(args.verify.read_bytes()) == report, "published results differ")
        print("Verified both independent boots, all process receipts and clock decisions")
    else:
        print(json.dumps(report, indent=2, sort_keys=True))
