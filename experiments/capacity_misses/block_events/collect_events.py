"""Count static block events on the unmodified, registered ELFs and inputs."""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import platform
import runpy
import shutil
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
PANELS = ("milan", "aonohako_shortest_paths")
EVENTS = ("direct", "indirect", "returns", "divide", "calls")
SEALED_READER = runpy.run_path(str(HERE.parents[2] / "collect.py"))
CONTROL = dict(instructions=44, direct=3, indirect=3, returns=6, divide=6)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(ok, message):
    if not ok:
        raise ValueError(message)


def checked_row(row, oracle):
    p = row["pid"]
    reader = SEALED_READER
    result = reader["aggregate"]({p: reader["parse_records"](p, row["sealed"].encode())}, p)
    events = dict(row["events"])
    require(row["stdout_sha256"] == oracle and events.pop("pid") == p and
            result["complete"] and result["valid"] and result["quality"] == "uninterrupted" and
            result["image_count"] == result["process_count"] == result["segments"][0]["threads"] == 1 and
            result["signals_observed"] == result["processes"][0]["exit_code"] == 0 and
            result["cpuid_profile"] == "x86-64-v2" and result["instructions"] == events.get("instructions"),
            "counter disagreement or invalid production receipt")
    require(set(events) == set(CONTROL) and all(type(i) is int and 0 <= i < 2**64 for i in events.values())
            and sum(events[e] for e in ("direct", "indirect", "returns", "divide")) <= events["instructions"],
            "invalid event tallies")
    return events


def registration(panel):
    doc = json.loads((HERE.parent / panel / "registration.json").read_bytes())
    if "payload" in doc:
        return doc["payload"]
    return {row["path"]: row["sha256"] for row in doc["files"]}


def verify_payload(panel, payload):
    plan = json.loads((HERE.parent / panel / "frozen.json").read_bytes())
    registered = registration(panel)
    names = {"plan.json", "probe.so"}
    names.update(name for name in registered if name.startswith("dynamorio/"))
    for name, case in plan["cases"].items():
        names.update(("bin/" + name, case["stdin"]))
    require(all(digest(payload / name) == registered[name] for name in names),
            "registered payload changed")
    require(digest(HERE.parent / panel / "frozen.json") == registered["plan.json"],
            "plan differs from registration")
    return plan


def quote(path):
    require(not any(c in str(path) for c in ('"', "'", "`", "\n", "\r", ";")), "unsupported client path")
    return '"' + str(path) + '"'


def trial(payload, runtime, client, binary, stdin, oracle):
    with tempfile.TemporaryDirectory() as tmp:
        slot = Path(tmp)
        cmd = [runtime / "bin64/drrun", "-root", runtime, "-quiet", "-client", payload / "probe.so",
               "00000000", f"-outdir {quote(slot)} -rep_bytes -latency_bound -cpuid_profile x86-64-v2",
               "-client", client, "00000001", "-out " + quote(slot / "events.json"),
               "-disable_traces", "--", binary]
        with stdin.open("rb") as inp, (slot / "stdout").open("wb") as out:
            with subprocess.Popen(cmd, cwd=payload, stdin=inp, stdout=out, stderr=subprocess.PIPE,
                                  env={"LC_ALL": "C", "PATH": os.defpath, "DYNAMORIO_CONFIGDIR": tmp}) as child:
                _, stderr = child.communicate(timeout=180)
        require(child.returncode == 0 and not stderr, f"{binary.name} failed: {stderr[:500]!r}")
        require(digest(slot / "stdout") == oracle, f"{binary.name} disagrees with stdout oracle")
        record, = slot.glob("process-*.jsonl")
        require(record.name == f"process-{child.pid}.jsonl", "process tree changed")
        row = dict(pid=child.pid, stdout_sha256=oracle, sealed=record.read_text(),
                   events=json.loads((slot / "events.json").read_bytes()))
        checked_row(row, oracle)
        return row


def collect(args):
    os.sched_setaffinity(0, {args.cpu})
    require(os.sched_getaffinity(0) == {args.cpu}, "cannot pin counter collection")
    sdk = args.sdk.resolve()
    client = args.client.resolve()
    control = args.control.resolve()
    deps = [Path("ext/lib64/release") / n for n in ("libdrx.so", "libdrreg.so")]
    report = dict(schema=1, cpu=args.cpu, machine=platform.machine(), kernel=platform.release(),
                  client_sha256=digest(client), source_sha256=digest(HERE / "probe.c"),
                  control_sha256=digest(control), extensions={str(p): digest(sdk/p) for p in deps},
                  panels={})
    for panel, payload in zip(PANELS, (args.milan.resolve(), args.paths.resolve())):
        plan = verify_payload(panel, payload)
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp) / "dynamorio"
            # The sealed runtime implements Durante's code-replay ABI. Supplement it
            # with stock SDK extensions without modifying the registered payload.
            shutil.copytree(payload / "dynamorio", runtime)
            for rel in deps:
                target = runtime / rel
                require(not target.exists(), "unexpected registered extension")
                shutil.copy2(sdk / rel, target)
            ctrl = trial(payload, runtime, client, control, Path(os.devnull), hashlib.sha256(b"").hexdigest())
            require({k: ctrl["events"][k] for k in CONTROL} == CONTROL, "assembly control")
            rows = []
            names = list(plan["cases"])
            for rep, order in enumerate((names, names[::-1])):
                for name in order:
                    case = plan["cases"][name]
                    row = trial(payload, runtime, client, payload / "bin" / name,
                                payload / case["stdin"], case["expected_sha256"])
                    rows.append(dict(case=name, rep=rep, **row))
            report["panels"][panel] = dict(plan_sha256=digest(payload / "plan.json"), control=ctrl, rows=rows)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("milan", "paths", "sdk", "client", "control", "out"):
        parser.add_argument("--" + option, type=Path, required=True)
    parser.add_argument("--cpu", type=int, required=True)
    args = parser.parse_args()
    report = collect(args)
    with args.out.open("xb") as output:
        output.write(gzip.compress((json.dumps(report, indent=2) + "\n").encode(), mtime=0))
