"""Verify retained counters and process CPU cost from both registered graph guests."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime
from fractions import Fraction
import importlib.util
import io
import json
from pathlib import Path
import statistics
import tarfile

import counters
import prepare

HERE, GRAPH, sha, read = prepare.HERE, prepare.GRAPH, prepare.sha, prepare.read
spec = importlib.util.spec_from_file_location("prior_milan_evidence", GRAPH.parent / "milan/replay.py")
milan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(milan)
require = milan.require
PHASES = ("primary", "repeat")
FROZEN = "20f032336810d30a49da2f9a24c4c9a35742145c"
REGISTRATION = "f0f488c3bc094e0407481781014caaf2d626d9107f5bc877554903cf60471c04"


def registered():
    plan, reg = read(HERE / "frozen.json"), read(HERE / "registration.json")
    earlier, prior = read(GRAPH / "frozen.json"), read(GRAPH / "registration.json")
    require(sha((HERE / "registration.json").read_bytes()) == REGISTRATION
            and sha((HERE / "frozen.json").read_bytes()) == reg["payload"]["plan.json"]
            and plan["protocol"] == read(HERE / "protocol.json")
            and tuple(plan["protocol"]["features"]) == counters.KEEP, "different frozen experiment")
    require(plan["ml4"] == earlier["ml4"] and plan["cases"] == earlier["cases"] and
            plan["deadline_us"] == earlier["protocol"]["deadline_us"] and
            plan["reference"] == earlier["protocol"]["reference"] and
            plan["clients"]["full"] == earlier["corrected_probe_sha256"] and
            reg["expected_rows"] == {".":63}, "different corpus, model, or original client")
    for name, digest in prior["payload"].items():
        if name not in ("guest.py", "plan.json"):
            require(reg["payload"]["full.so" if name == "probe.so" else name] == digest, "different parent file")
    for name in ("guest.py", "counters.py"):
        require(sha((HERE / name).read_bytes()) == reg["payload"][name], "changed collector")
    for arm, digest in plan["clients"].items():
        require(reg["payload"][arm + ".so"] == digest, "changed registered client")
    return plan, reg


def read_boot(phase, plan, reg, cloud):
    raw = (HERE / "evidence" / (phase + ".tar.gz")).read_bytes()
    host = cloud["platform"]
    require(sha(raw) == cloud["raw_sha256"] and reg["archive_sha256"] == cloud["archive_sha256"]
            and cloud["registration_commit"] == FROZEN and cloud["exit"] == 0
            and datetime.fromisoformat(cloud["started_at"]) < datetime.fromisoformat(host["created_at"])
            < datetime.fromisoformat(cloud["cleanup"]["verified_at"])
            and host["cpu_platform"] == "AMD Milan" and host["machine_type"] == "n2d-standard-4"
            and host["threads_per_core"] == 1 and host["disk_auto_delete"] and not host["external_ip"]
            and not host["service_account"] and cloud["cleanup"]["instance_http"] == cloud["cleanup"]["boot_disk_http"] == 404
            and cloud["cleanup"]["key_removed"], "invalid GCE platform, registration or lifecycle")
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
        def get(name):
            return archive.extractfile("output/" + name).read()
        guest = json.loads(get("platform.json"))
        require(guest["phase"] == phase and guest["cpus"] == [1] and guest["boot_sha256"] == cloud["boot_sha256"]
                and guest["plan_sha256"] == reg["payload"]["plan.json"], "unexpected guest")
        trace = defaultdict(list)
        for cpu, name, pid, old in milan.EXEC.findall(get("exec-trace.txt").decode()):
            if pid == old:
                trace[int(pid)].append((int(cpu), name))
        names = list(plan["cases"])
        sequence = plan["protocol"]["rounds"]
        if phase == "repeat":
            names.reverse()
            sequence = [{"reduced":"full", "full":"reduced"}.get(a, a) for a in sequence]
        schedule = [(a, names[(round_no + j) % len(names)]) for round_no, a in enumerate(sequence)
                    for j in range(len(names))]
        rows = [json.loads(l) for l in get("trials.jsonl").splitlines()]
        require(len(rows) == len(schedule) == reg["expected_rows"]["."]
                and len({r["pid"] for r in rows}) == len(rows), "incomplete execution matrix")
        samples = defaultdict(lambda: defaultdict(list))
        for i, (row, (arm, name)) in enumerate(zip(rows, schedule, strict=True)):
            require((row["index"], row["arm"], row["case"]) == (i, arm, name) and row["exit_code"] == 0
                    and row["stdout_sha256"] == plan["cases"][name]["expected_sha256"]
                    and min(row["user_us"], row["system_us"]) >= 0, "different case or execution")
            cpu = row["user_us"] + row["system_us"]
            require(cpu > 0, "invalid CPU time")
            pid = row["pid"]
            app = "/var/tmp/durante-pr/"
            commands = [app+"bin/"+name] if arm == "native" else [app+"dynamorio/bin64/drrun",
                        app+"dynamorio/lib64/release/libdynamorio.so"]
            require(trace[pid] == [(1, cmd) for cmd in commands], "wrong CPU, image or process")
            sample = dict(cpu_us=cpu)
            if arm != "native":
                directory = f"output/records-{i:03d}/"
                key = f"process-{pid}.jsonl"
                files = [m.name for m in archive.getmembers() if m.isfile() and m.name.startswith(directory)]
                require(files == [directory + key], "different process record set")
                records = milan.collector["parse_records"](pid, get(f"records-{i:03d}/" + key))
                report = milan.collector["aggregate"]({pid:records}, pid)
                stderr = get(f"probe-{i:03d}.txt")
                values, skipped = counters.counters(arm, report, stderr, milan.capacity)
                require(row["features"] == values and row["skipped"] == skipped, "feature/coverage receipt mismatch")
                sample.update(features=values, skipped=skipped)
                if arm == "full":
                    l1, outer = stderr.splitlines()
                    l1 = milan.capacity["PROBE_L1D"].fullmatch(l1)
                    o = list(map(int, milan.capacity["PROBE_OUTER"].fullmatch(outer).groups()))
                    sample["all_features"] = dict(values, l1d_misses=int(l1.group(3)), random_2m=o[5]-o[8],
                                                  random_32m=o[6]-o[9])
            samples[name][arm].append(sample)
        steal = int(guest["after"].split()[8]) - int(guest["before"].split()[8])
        require(steal >= 0, "negative steal ticks")
        return samples, steal


def ratio_interval(numerator, denominator):
    return [round(min(numerator) / max(denominator), 3), round(max(numerator) / min(denominator), 3)]


def label(bounds, deadline):
    if max(bounds) <= deadline:
        return "accept"
    if min(bounds) > deadline:
        return "timeout"
    return "indeterminate"


def summarize(samples, plan):
    weights = {k:Fraction(str(v)) for k, v in plan["ml4"].items()}
    scores = {}
    for n, arms in samples.items():
        scores[n] = {}
        for arm, key in (("full", "all_features"), ("reduced", "features")):
            scores[n][arm] = [sum(weights[k] * v for k, v in row[key].items()) for row in arms[arm]]
    reference = plan["reference"]
    reference_cpu = [r["cpu_us"] for r in samples[reference]["native"]]
    table, totals = {}, {a: Counter() for a in ("full", "reduced")}
    for name, arms in samples.items():
        cpu = {a:[r["cpu_us"] for r in rows] for a, rows in arms.items()}
        values = [r["features"] for a in ("full", "reduced") for r in arms[a]]
        coverage = [r["skipped"] for a in ("full", "reduced") for r in arms[a]]
        match = all(v == values[0] for v in values) and len(set(coverage)) == 1
        stats = table[name] = dict(retained_and_skipped_identical=match, skipped_range=[min(coverage), max(coverage)],
            samples={a:len(v) for a, v in cpu.items()},
            cpu_us={a:[min(v), statistics.median(v), max(v)] for a,v in cpu.items()},
            speedup=round(statistics.median(cpu["full"]) / statistics.median(cpu["reduced"]), 3),
            speedup_range=ratio_interval(cpu["full"], cpu["reduced"]),
            overhead={a:round(statistics.median(cpu[a])/statistics.median(cpu["native"]), 3) for a in ("full","reduced")})
        stats["verdicts"] = {"native":label(cpu["native"], plan["deadline_us"])}
        stats["estimated_us"] = {}
        stats["relative_error_percent"] = {}
        for arm in ("full", "reduced"):
            target, anchor = scores[name][arm], scores[reference][arm]
            require(min(target) > 0 and min(anchor) > 0, "nonpositive model/reference score")
            bounds = [min(target)*min(reference_cpu)/max(anchor), max(target)*max(reference_cpu)/min(anchor)]
            stats["estimated_us"][arm] = [round(float(b), 3) for b in bounds]
            stats["relative_error_percent"][arm] = [
                round(float(100 * (bounds[0] / max(cpu["native"]) - 1)), 3),
                round(float(100 * (bounds[1] / min(cpu["native"]) - 1)), 3),
            ]
            actual, pred = stats["verdicts"]["native"], label(bounds, plan["deadline_us"])
            stats["verdicts"][arm] = pred
            if name != reference:
                outcome = ("indeterminate" if "indeterminate" in (pred, actual) else "agree" if actual == pred
                           else "false_accept" if pred == "accept" else "false_timeout")
                totals[arm][outcome] += 1
    return dict(cases=table, excluding_reference=totals)


def replay():
    plan, reg = registered()
    paths = HERE / "evidence"
    cloud = read(paths / "cloud.json")
    previous = [v for loc in (GRAPH, GRAPH.parent / "milan") for v in read(loc / "evidence/cloud.json").values()]
    for field in ("boot_sha256", "instance_id", "boot_disk_id"):
        ids = {h[field] for h in cloud.values()}
        require(len(ids) == 2 and ids.isdisjoint(x[field] for x in previous), "reused guest " + field)
    panels, result = {}, {}
    for phase in PHASES:
        panel, steal = read_boot(phase, plan, reg, cloud[phase])
        panels[phase] = panel
        result[phase] = dict(**summarize(panel, plan), steal_ticks=steal)
    pooled = {n:{a:panels["primary"][n][a]+panels["repeat"][n][a] for a in ("native","full","reduced")} for n in plan["cases"]}
    result["pooled"] = summarize(pooled, plan)
    return result


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--verify", type=Path)
    args = cli.parse_args()
    encoded = json.dumps(replay(), indent=2, sort_keys=True)
    if args.verify:
        require(json.loads(encoded) == read(args.verify), "published result differs")
        print("Verified both pinned boots, all feature vectors, costs and simulated verdicts")
    else:
        print(encoded)
