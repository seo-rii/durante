#!/usr/bin/env python3
"""Match each wait4 child PID to its execs in the GCE guest's scheduler trace."""
import argparse
from collections import Counter
from decimal import Decimal
import gzip
import hashlib
import json
from pathlib import Path
import re

from sample_layout import sample_layout


LAUNCHER = "/opt/durante/dynamorio/bin64/drrun"
LOADER = "/opt/durante/dynamorio/lib64/release/libdynamorio.so"
TRACE_EVENTS = {"sched/sched_process_exec", "sched/sched_process_fork",
                "sched/sched_process_exit", "cgroup/cgroup_attach_task"}
ROW = re.compile(r"\[(\d+)\]\s+\S+\s+(\d+\.\d+):\s+(\w+):\s+(.*)$")


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read(path):
    raw = path.read_bytes()
    return gzip.decompress(raw) if path.suffix == ".gz" else raw


def field(details, key):
    return int(re.search(r"(?:^| )" + key + r"=(\d+)(?: |$)", details)[1])


def placement(dataset, receipt):
    expected = []
    for row, _, phases in sample_layout(dataset):
        for phase in phases:
            pid = row[phase]["pid"]
            paths = [LAUNCHER, LOADER] if phase in ("metered", "full", "fast") else ["/out/bin/" + row["workload_id"] + "/Main"]
            expected.append((pid, paths))
    require(len({pid for pid, _ in expected}) == len(expected), "duplicate child PID")
    children = {pid: [] for pid, _ in expected}
    last_time = 0
    for event in receipt["events"]:
        kind, ns, cpu, pid, *args = event
        require(cpu == 1 and ns >= last_time and pid in children, "misplaced or unexpected child event")
        last_time = ns
        if kind == "attach":
            require(args == [receipt["cgroup_sha256"]], "child left pinned cgroup")
        else:
            children[pid].append((kind, ns, args))

    last_exit = 0
    for pid, paths in expected:
        process = children[pid]
        kinds = ["fork"] + ["exec"] * len(paths) + ["exit"]
        require([kind for kind, _, _ in process] == kinds, f"wrong phase lifecycle for PID {pid}")
        require(process[0][2] == [dataset["collector_pid"]] and process[0][1] >= last_exit,
                f"phase PID {pid} has wrong parent or overlaps previous phase")
        require([args for _, _, args in process[1:-1]] == [[path] for path in paths],
                f"wrong executable for PID {pid}")
        last_exit = process[-1][1]
    require(receipt["collector_observed"] and receipt["collector_attached"] and
            receipt["collector_pid"] == dataset["collector_pid"] and
            receipt["cpuset_requested"] == receipt["cpuset_effective"] == "1" and
            receipt["observer_cpus"] == [0] and receipt["sampled_last_cpus"] == [1] and
            receipt["sampled_allowed_masks"] == [[1]] and receipt["sampled_tids"] > 0 and receipt["polls"] > 0,
            "incomplete independent CPU/cgroup observations")
    cpus = receipt["per_cpu"]
    require(set(cpus) == {"0", "1"} and all(
        c["entries"] == c["parsed"] and c["entries"] > 0 and
        all(c[key] == 0 for key in ("overrun", "commit overrun", "dropped events", "read events"))
        for c in cpus.values()), "lost or unaccounted trace events")


def reduce(dataset, folder):
    snap, stop, sampler = [json.loads((folder / name).read_bytes()) for name in
                           ("start.json", "stop.json", "samples.json")]
    first, last = snap["before"], stop["after"]
    keys = ("path", "init_pid", "init_start_ticks", "boot_id", "guest_id_sha256", "cpuset_requested",
            "cpuset_effective", "allowed_cpus", "start_or_end_cpu", "observer_allowed_cpus")
    require(all(first[k] == last[k] for k in keys) and first["allowed_cpus"] == [1] and
            first["start_or_end_cpu"] == snap["cpu"] == 1, "guest, cgroup or CPU placement changed")
    require(snap["online"] == "0-1" and int(snap["trace_cpumask"].replace(",", ""), 16) == 3 and
            "[mono]" in snap["trace_clock"].split() and set(snap["events"]) == TRACE_EVENTS,
            "trace does not cover the expected CPUs/events")
    raw = (folder / "events.trace").read_bytes()
    require(sha(raw) == stop["event_sha256"], "guest trace digest mismatch")
    group = first["path"]
    root = dataset["collector_pid"]
    pids = {row[phase]["pid"] for row, _, phases in sample_layout(dataset) for phase in phases}
    totals = Counter()
    events = []
    root_group = ""
    root_exec_in_group = False
    for line in raw.decode().splitlines():
        if line.startswith("#") or not line.strip():
            continue
        match = ROW.search(line)
        require(match is not None, "unrecognized scheduler trace row")
        cpu, timestamp, name, details = match.groups()
        cpu = int(cpu)
        totals[cpu] += 1
        ns = int(Decimal(timestamp) * 1_000_000_000)
        pid = field(details, "pid")
        if name == "sched_process_fork":
            child = field(details, "child_pid")
            if child in pids:
                require(pid == root and root_exec_in_group and root_group == group,
                        "measured child forked outside the pinned collector cgroup")
                events.append(["fork", ns, cpu, child, pid])
        elif name == "sched_process_exec":
            path = details.split("filename=", 1)[1].rsplit(" pid=", 1)[0]
            if pid == root:
                root_exec_in_group = root_group == group and cpu == 1 and path == "/usr/bin/python3"
            if pid in pids:
                require(pid == field(details, "old_pid"), "child PID changed at exec")
                if path == "/opt/durante/dynamorio/bin64/../lib64/release/libdynamorio.so":
                    path = LOADER
                events.append(["exec", ns, cpu, pid, path])
        elif name == "sched_process_exit":
            if pid in pids:
                events.append(["exit", ns, cpu, pid])
        elif name == "cgroup_attach_task":
            dest = re.search(r"(?:^| )dst_path=(\S+)", details)[1]
            if pid == root:
                root_group = dest
            if pid in pids:
                events.append(["attach", ns, cpu, pid, sha(dest.encode())])
        else:
            raise ValueError("unexpected tracepoint " + name)
    require(set(stop["stats"]) == {"cpu0", "cpu1"} and set(totals) == {0, 1}, "wrong trace CPU set")
    stats = {str(cpu): {key: int(stop["stats"]["cpu" + str(cpu)][key]) for key in
                        ("entries", "overrun", "commit overrun", "dropped events", "read events")}
             | {"parsed": totals[cpu]} for cpu in totals}
    tasks = sampler["tasks"]
    require(all(t["cgroup"] == group for t in tasks), "observer encountered another cgroup")

    def steal(snapshot):
        return int(next(line for line in snapshot["proc_stat"] if line.startswith("cpu1 ")).split()[8])

    receipt = {
        "schema": 1, "raw_trace_sha256": sha(raw), "guest_id_sha256": first["guest_id_sha256"],
        "cgroup_sha256": sha(group.encode()), "collector_pid": root,
        "collector_observed": any(t["tgid"] == root for t in tasks),
        "collector_attached": root_exec_in_group and root_group == group,
        "cpuset_requested": first["cpuset_requested"], "cpuset_effective": first["cpuset_effective"],
        "observer_cpus": first["observer_allowed_cpus"], "sampled_tids": len({t["tid"] for t in tasks}),
        "sampled_last_cpus": sorted({cpu for t in tasks for cpu in t["cpu"]}),
        "sampled_allowed_masks": sorted([list(v) for v in {tuple(m) for t in tasks for m in t["masks"]}]),
        "polls": sampler["polls"], "steal_ticks_before_after": [steal(first), steal(last)],
        "per_cpu": stats, "events": events,
    }
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("receipt", type=Path, help="compressed, public placement receipt")
    parser.add_argument("--capture", type=Path, help="create receipt from the raw GCE trace directory")
    parser.add_argument("--platform", type=Path, help="cross-check the GCE instance, container and cleanup record")
    args = parser.parse_args()
    raw = read(args.dataset)
    dataset = json.loads(raw)
    if args.capture:
        receipt = reduce(dataset, args.capture)
        receipt["dataset_sha256"] = sha(raw)
        placement(dataset, receipt)
        args.receipt.write_bytes(gzip.compress((json.dumps(receipt, separators=(",", ":")) + "\n").encode(), mtime=0))
    else:
        receipt = json.loads(read(args.receipt))
        require(receipt["schema"] == 1 and receipt["dataset_sha256"] == sha(raw), "receipt dataset mismatch")
        placement(dataset, receipt)
    if args.platform:
        platform = json.loads(args.platform.read_bytes())
        require(platform["target"] == "compute_engine" and platform["threads_per_core"] == 1 and
                platform["benchmark_cpu"] == 1 and platform["observer_cpus"] == [0] and
                platform["guest_id_sha256"] == receipt["guest_id_sha256"] and
                platform["trace_raw_sha256"] == receipt["raw_trace_sha256"] and
                platform["dataset_sha256"] == receipt["dataset_sha256"] and
                platform["tools"] == dataset["tools"] and
                platform["affinity_observer_polls"] == receipt["polls"] and
                platform["cleanup"]["instance_http"] == platform["cleanup"]["boot_disk_http"] == 404,
                "platform record disagrees with the measurement or cleanup")
    counts = Counter(event[4] for event in receipt["events"] if event[0] == "exec")
    native = sum(n for name, n in counts.items() if name.endswith("/Main"))
    print(f"Verified {native} native execs, {counts[LAUNCHER]} launches and {counts[LOADER]} same-PID loaders on guest CPU 1.")


if __name__ == "__main__":
    main()
