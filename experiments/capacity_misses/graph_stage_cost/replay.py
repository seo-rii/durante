"""Replay five instrumented-mode costs and their common counters from raw guest evidence."""
import argparse
from collections import defaultdict
from datetime import datetime
import importlib.util
import io
import json
from pathlib import Path
import statistics
import tarfile

import prepare

HERE = prepare.HERE
spec = importlib.util.spec_from_file_location("milan_evidence", HERE.parent / "milan/replay.py")
milan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(milan)
sha, require = milan.sha, milan.require
MODES = ("native", "rep", "latency", "l1d", "outer")
PHASES = ("primary", "repeat")
FREEZE = "163181754647420beb9f603e91ad30d1d21175fd"
REG = "b69d402696cc33c415f9956831cefaa6f66d1c864ad57309382447ce4aa4aaf4"


def read(path):
    return json.loads(path.read_bytes())


def registered():
    manifest = read(HERE / "registration.json")
    plan = read(HERE / "frozen.json")
    parent = read(prepare.PARENT / "registration.json")
    previous = read(prepare.PARENT / "frozen.json")
    require(sha((HERE / "registration.json").read_bytes()) == REG and
            sha((HERE / "frozen.json").read_bytes()) == manifest["payload"]["plan.json"] and
            sha((HERE / "guest.py").read_bytes()) == manifest["payload"]["guest.py"] and
            read(HERE / "protocol.json") == plan["protocol"] and
            list(plan["protocol"]["modes"]) == list(MODES), "different registration or collector")
    require(manifest["expected_rows"] == {".": 75} and
            plan["cases"] == {n: previous["cases"][n] for n in plan["protocol"]["programs"]} and
            plan["client_sha256"] == previous["corrected_probe_sha256"], "different graph panel")
    for path, digest in manifest["payload"].items():
        if path not in ("guest.py", "plan.json"):
            require(parent["payload"][path] == digest, "changed original executable or helper")
    return plan, manifest


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
    expected = {"rep": 0, "latency": 0, "l1d": 1, "outer": 2}[arm]
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


def read_boot(phase, host, reg, plan):
    path = HERE / "evidence" / (phase + ".tar.gz")
    raw = path.read_bytes()
    p, end = host["platform"], host["cleanup"]
    require(host["exit"] == 0 and host["raw_sha256"] == sha(raw) and
            host["archive_sha256"] == reg["archive_sha256"] and host["registration_commit"] == FREEZE and
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
        samples = {name:{arm:[] for arm in MODES} for name in names}
        for i, (r, (arm, name)) in enumerate(zip(runs, sequence, strict=True)):
            require((r["index"], r["case"], r["arm"]) == (i, name, arm) and r["exit_code"] == 0 and
                    r["stdout_sha256"] == plan["cases"][name]["expected_sha256"], "invalid case or receipt")
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


def aggregate(arms):
    times = {arm:[r["cpu_us"] for r in runs] for arm, runs in arms.items()}
    med = {arm:statistics.median(t) for arm, t in times.items()}
    unique = defaultdict(set)
    for records in arms.values():
        for row in records:
            for key, val in row["counters"].items():
                unique[key].add(val)
    total = med["outer"] - med["native"]
    require(total > 0, "full mode did not exceed native")
    stages = {}
    for before, after in zip(MODES, MODES[1:]):
        delta = med[after] - med[before]
        possible = [t-s for s in times[before] for t in times[after]]
        stages[after] = dict(delta_us=delta, delta_range_us=[min(possible), max(possible)],
                             percent_full_cpu=round(100 * delta / med["outer"], 3),
                             percent_excess_over_native=round(100 * delta / total, 3))
    l1d = [row["counters"] for arm in ("l1d", "outer") for row in arms[arm]]
    line_hits = [100 * (1 - c["l1d_misses"] / c["l1d_lines"]) for c in l1d]
    return dict(l1d_line_hit_percent=[round(min(line_hits), 3), round(max(line_hits), 3)],
                samples={a:len(t) for a,t in times.items()}, cpu_us={a:[min(t), med[a], max(t)] for a,t in times.items()},
                over_native={a:round(med[a]/med["native"], 3) for a in MODES},
                distinct_readings_per_counter={k:len(v) for k,v in unique.items()}, stages=stages)


def replay():
    plan, reg = registered()
    cloud = read(HERE / "evidence/cloud.json")
    prior = list(read(prepare.PARENT / "evidence/cloud.json").values())
    for key in ("instance_id", "boot_disk_id", "boot_sha256"):
        ids = {h[key] for h in cloud.values()}
        require(len(ids) == 2 and ids.isdisjoint(h[key] for h in prior), "reused " + key)
    result, observations = {}, {}
    for phase in PHASES:
        samples, steal = read_boot(phase, cloud[phase], reg, plan)
        result[phase] = dict(cases={n:aggregate(a) for n,a in samples.items()}, steal_ticks=steal)
        observations[phase] = samples
    pooled = {n:{arm:observations["primary"][n][arm] + observations["repeat"][n][arm] for arm in MODES}
              for n in plan["cases"]}
    result["pooled"] = {n:aggregate(arms) for n,arms in pooled.items()}
    return result


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--verify", type=Path)
    args = cli.parse_args()
    result = replay()
    if args.verify:
        require(result == read(args.verify), "published numbers differ")
        print("Verified graph stages, costs and shared counters on both pinned guests.")
    else:
        print(json.dumps(result, indent=2, sort_keys=True))
