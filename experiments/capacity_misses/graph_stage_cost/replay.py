"""Replay five instrumented-mode costs and their common counters from raw guest evidence."""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import statistics

import prepare

from graph_evidence import read_boot, require, sha

HERE = prepare.HERE
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
        samples, steal = read_boot(phase, cloud[phase], reg, plan, evidence=HERE / "evidence", freeze=FREEZE)
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
