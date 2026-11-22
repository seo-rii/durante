"""Publish the exact trial receipts and PID-linked exec placement from a completed capture."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import tarfile


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def parse_trace(text):
    events = {}
    raw_lines = []
    for line in text.splitlines():
        match = re.search(r"\[(\d+)\].*sched_process_exec: filename=(\S+) pid=(\d+) old_pid=\d+", line)
        if match:
            cpu, path, pid = match.groups()
            events.setdefault(int(pid), []).append(dict(cpu=int(cpu), executable=Path(path).name))
            raw_lines.append((int(pid), line))
    return events, raw_lines


def publish(root, out):
    cleanup = json.loads((root / "cleanup.json").read_bytes())
    require((root / "ops.exit").read_text().strip() == "0" and cleanup["instance_http"] == cleanup["boot_disk_http"] == 404
            and cleanup["key_removed"] is True, "cleanup receipt is incomplete")
    protocol = (root / "payload/protocol.json").read_bytes()
    plan = json.loads(protocol)
    cases = {c["case_id"]: c for c in json.loads((root / "payload/cases.json").read_bytes())}
    platform = json.loads((root / "platform.json").read_bytes())
    require(platform["cpu_platform"] == "AMD Milan" and platform["machine_type"] == "n2d-standard-4"
            and platform["threads_per_core"] == 1, "unexpected provider placement")
    del platform["creation"]
    out.mkdir(exist_ok=True)
    with tarfile.open(root / "raw.tar.gz", "r:gz") as capture, tarfile.open(out / "receipts.tar.xz", "w:xz") as archive:
        contents = {m.name.removeprefix("output/"): capture.extractfile(m).read()
                    for m in capture if m.isfile() and m.name.startswith("output/")}
        read = contents.__getitem__
        actual = {path for path in contents if path.startswith("runs/")}
        events, trace_lines = parse_trace(read("exec-trace").decode())
        stats = read("trace-stats").decode()
        for field in ("overrun", "dropped events"):
            values = [int(s) for s in re.findall("^" + field + r":\s*(\d+)", stats, re.M)]
            require(len(values) == 2 and not any(values), "trace lost events")
        data = json.loads(read("dataset.json"))
        rows = data["rows"]
        require(data["protocol_sha256"] == digest(protocol) and data["platform"]["affinity"] == [plan["cpu"]]
                and data["platform"]["cgroup_allowed_cpus"] == str(plan["cpu"]), "guest identity")
        data["platform"].update(**platform, max_trace_overrun=0, max_trace_dropped_events=0)
        require([json.loads(s) for s in read("progress.jsonl").splitlines()] == rows, "progress disagrees with dataset")
        used_paths = set()
        pids = set()
        placement = []
        for index, (row, expected) in enumerate(zip(rows, plan["schedule"], strict=True)):
            require(row["sequence"] == index and all(row[k] == v for k, v in expected.items()), "schedule mismatch")
            prefix = f"runs/{index:03d}/"
            pid = row["launcher_pid"]
            require(pid > 0 and pid not in pids and row["exit_code"] == 0 and
                    row["user_us"] + row["sys_us"] == row["cpu_us"] > 0, "invalid trial row")
            pids.add(pid)
            arm = row["arm"]
            case = cases[row["case_id"]]
            native = arm in ("native", "warmup")
            names = {"stdout", "stderr"}
            if not native:
                names.update({"production.json", f"records/process-{pid}.jsonl"})
            if arm in ("diag_near", "diag_span", "live_span"):
                names.add("sample.json")
            if arm in ("live", "live_span"):
                names.add("clock.bin")
            require(set(row["receipts"]) == {prefix + name for name in names}, "receipt member set")
            for path, sha in sorted(row["receipts"].items()):
                content = read(path)
                require(path not in used_paths and digest(content) == sha, "receipt changed")
                used_paths.add(path)
                entry = tarfile.TarInfo(path)
                entry.mode, entry.mtime, entry.size = 0o644, 0, len(content)
                archive.addfile(entry, io.BytesIO(content))
            require(row["stdout_sha256"] == case["stdout_sha256"] == digest(read(prefix + "stdout"))
                    and not read(prefix + "stderr"), "stdio mismatch")
            names = [case["program"]] if native else ["drrun", "libdynamorio.so"]
            require(events.get(pid) == [dict(cpu=plan["cpu"], executable=name) for name in names], "placement mismatch")
            placement.append(dict(sequence=index, pid=pid, events=events[pid]))
        require(actual == used_paths, "unmanifested capture member")
    (out / "exec-trace.txt").write_text("".join(line + "\n" for pid, line in trace_lines if pid in pids))
    (out / "trace-stats.txt").write_text(stats)
    save(out / "dataset.json", data)
    save(out / "placement.json", placement)
    save(out / "cleanup.json", cleanup)
    print(f"exported {len(rows)} trials and {len(used_paths)} receipts, all on CPU {plan['cpu']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument("out", type=Path)
    args = parser.parse_args()
    publish(args.capture, args.out)
