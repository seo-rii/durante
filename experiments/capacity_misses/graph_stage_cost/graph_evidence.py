"""Validate graph diagnostic process receipts, CPU affinity and guest archives."""
from collections import defaultdict
from datetime import datetime
import importlib.util
import io
import json
from pathlib import Path
import tarfile

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("milan_evidence", HERE.parent / "milan/replay.py")
milan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(milan)
sha, require = milan.sha, milan.require


def decode(pid, arm, process, stderr):
    records = milan.collector["parse_records"](pid, process)
    report = milan.collector["aggregate"]({pid: records}, pid)
    require(report["valid"] and report["complete"] and report["quality"] == "uninterrupted" and
            report["signals_observed"] == 0 and report["root_pid"] == pid and
            report["process_count"] == report["image_count"] == report["segments"][0]["threads"] == 1 and
            report["rep_telemetry_status"] == "movs_stos_only" and report["cpuid_profile"] == "x86-64-v2" and
            report["processes"][0]["exit_code"] == 0, "invalid process record")
    features = {k:report[k] for k in ("instructions", "rep_bytes", "rep_entries", "unsupported_rep_entries")}
    features.update(report["rep_breakdown"])
    require(("latency_bound" in report) == (arm != "rep"), "incorrect latency capability")
    if "latency_bound" in report:
        features["latency_x16"] = report["latency_bound"]["cycles_x16"]
    lines = stderr.splitlines()
    expected = {"rep": 0, "latency": 0, "callback": 0, "l1d": 1, "outer": 2}[arm]
    require(len(lines) == expected, "different cache mode")
    if lines:
        l1 = milan.capacity["PROBE_L1D"].fullmatch(lines[0])
        require(l1 is not None, "invalid L1D report")
        keys = ("l1d_refs", "l1d_lines", "l1d_misses", "l1d_skipped", "l1d_rep")
        features.update(zip(keys, map(int, l1.groups()), strict=True))
    if arm == "outer":
        outer = milan.capacity["PROBE_OUTER"].fullmatch(lines[1])
        require(outer is not None, "invalid outer report")
        parts = list(map(int, outer.groups()))
        require(parts[:3] == [4096, 32768, 524288] and parts[3] == features["l1d_misses"],
                "different outer hierarchy or L1D input")
        features["outer"] = tuple(parts)
    return features


def read_boot(phase, host, reg, plan, *, evidence, freeze, client_by_arm=None):
    path = evidence / (phase + ".tar.gz")
    raw = path.read_bytes()
    p, end = host["platform"], host["cleanup"]
    require(host["exit"] == 0 and host["raw_sha256"] == sha(raw) and
            host["archive_sha256"] == reg["archive_sha256"] and host["registration_commit"] == freeze and
            datetime.fromisoformat(host["started_at"]) < datetime.fromisoformat(p["created_at"])
                < datetime.fromisoformat(end["verified_at"]) and
            p["cpu_platform"] == "AMD Milan" and p["machine_type"] == "n2d-standard-4" and
            p["threads_per_core"] == 1 and p["disk_auto_delete"] and not p["external_ip"] and
            not p["service_account"] and end["instance_http"] == end["boot_disk_http"] == 404 and end["key_removed"],
            "different platform, archive or lifecycle")
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as a:
        def get(name):
            return a.extractfile("output/" + name).read()
        platform = json.loads(get("platform.json"))
        require(platform["phase"] == phase and platform["cpus"] == [1] and
                platform["boot_sha256"] == host["boot_sha256"] and
                platform["plan_sha256"] == reg["payload"]["plan.json"], "invalid guest identity")
        runs = [json.loads(line) for line in get("trials.jsonl").splitlines()]
        names = list(plan["cases"])
        sequence = [(arm, names[(round_no+j) % len(names)]) for round_no, arm in enumerate(plan["protocol"]["rounds"])
                    for j in range(len(names))]
        if phase == "repeat":
            sequence.reverse()
        require(len(runs) == len(sequence) == reg["expected_rows"]["."] and
                len({r["pid"] for r in runs}) == len(runs), "incomplete execution matrix")
        trace = defaultdict(list)
        for cpu, image, pid, prev in milan.EXEC.findall(get("exec-trace.txt").decode()):
            if pid == prev:
                trace[int(pid)].append((int(cpu), image))
        files = {m.name for m in a if m.isfile()}
        samples = {name:{arm:[] for arm in plan["protocol"]["modes"]} for name in names}
        for i, (r, (arm, name)) in enumerate(zip(runs, sequence, strict=True)):
            require((r["index"], r["case"], r["arm"]) == (i, name, arm) and r["exit_code"] == 0 and
                    r["stdout_sha256"] == plan["cases"][name]["expected_sha256"], "invalid case or receipt")
            if client_by_arm is not None:
                require(r.get("client_sha256") == client_by_arm[arm], "incorrect installed diagnostic")
            pid, prefix = r["pid"], "/var/tmp/durante-pr/"
            images = ["bin/"+name] if arm == "native" else ["dynamorio/bin64/drrun", "dynamorio/lib64/release/libdynamorio.so"]
            require(trace[pid] == [(1, prefix+x) for x in images], "unexpected exec or CPU")
            cpu = r["user_us"] + r["system_us"]
            require(cpu > 0 and min(r["user_us"], r["system_us"]) >= 0, "invalid process time")
            directory, stderr = f"records-{i:03d}/", f"probe-{i:03d}.txt"
            record = directory + f"process-{pid}.jsonl"
            records = {n for n in files if n.startswith("output/" + directory)}
            features = {}
            if arm == "native":
                require(not records and "output/" + stderr not in files, "unexpected native diagnostic")
            else:
                require(records == {"output/" + record}, "different process record set")
                features = decode(pid, arm, get(record), get(stderr))
            samples[name][arm].append(dict(cpu_us=cpu, counters=features))
    steal = int(platform["after"].split()[8]) - int(platform["before"].split()[8])
    require(steal >= 0, "negative steal ticks")
    return samples, steal
