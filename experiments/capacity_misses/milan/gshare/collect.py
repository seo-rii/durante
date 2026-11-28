"""Count the registered, single-threaded Milan binaries with Durante's gshare client."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import subprocess
import sys

HERE = Path(__file__).resolve().parent
EXPERIMENTS = HERE.parents[2]
sys.path.insert(0, str(EXPERIMENTS))
from collect_branch_model import branch_counts
from collect_load_model import quoted
from collect import collect as aggregate_meter


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cpu", type=int, required=True)
    args = parser.parse_args()
    payload, probe, output = (p.resolve() for p in (args.payload, args.probe, args.output))
    os.sched_setaffinity(0, {args.cpu})
    _, hard = resource.getrlimit(resource.RLIMIT_STACK)
    resource.setrlimit(resource.RLIMIT_STACK, (hard, hard))
    output.mkdir(mode=0o700)
    plan = json.loads((HERE.parent / "frozen.json").read_bytes())
    registration = json.loads((HERE.parent / "registration.json").read_bytes())
    registered = {item["path"]: item["sha256"] for item in registration["files"]}
    tools = {"drrun": payload / "dynamorio/bin64/drrun", "meter": payload / "probe.so", "probe": probe}
    for key in ("drrun", "meter"):
        path = tools[key]
        if digest(path) != registered[str(path.relative_to(payload))]:
            raise ValueError("runtime differs from the registered Milan payload")
    names = list(plan["cases"])
    rows = []
    for rep, order in enumerate((names, names[::-1]), 1):
        for name in order:
            case = plan["cases"][name]
            exe, stdin = payload / "bin" / name, payload / case["stdin"]
            if digest(exe) != case["elf_sha256"] or digest(stdin) != case["stdin_sha256"]:
                raise ValueError(f"{name}: changed program or input")
            run = output / f"{rep}-{name}"
            records = run / "records"
            records.mkdir(parents=True)
            path = run / "features.json"
            meter_options = f"-outdir {quoted(records)} -rep_bytes -cpuid_profile x86-64-v2 -latency_bound"
            command = [tools["drrun"], "-quiet", "-client", tools["meter"], "00000000", meter_options,
                       "-client", probe, "00000001", f"-out {quoted(path)} -bpred",
                       "-follow_children", "-disable_traces", "-vm_size", "32M", "-vmheap_size", "32M", "--", exe]
            with stdin.open("rb") as source, (run / "stdout").open("wb") as out:
                with subprocess.Popen(command, stdin=source, stdout=out, stderr=subprocess.PIPE,
                                      env={"PATH": os.defpath, "LC_ALL": "C", "DYNAMORIO_CONFIGDIR": str(run)}) as child:
                    try:
                        _, stderr = child.communicate(timeout=180)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.communicate()
                        raise
                    if child.returncode != 0 or stderr:
                        raise RuntimeError(f"{name}: exit {child.returncode}, {stderr[:512]!r}")
                    pid = child.pid
            if digest(run / "stdout") != case["expected_sha256"]:
                raise ValueError(f"{name}: changed output")
            receipt = aggregate_meter(records, pid)
            if not (receipt["valid"] and receipt["complete"] and receipt["cpuid_profile"] == "x86-64-v2"
                    and receipt["process_count"] == receipt["image_count"] == receipt["segments"][0]["threads"] == 1):
                raise ValueError(f"{name}: incomplete or incompatible meter receipt")
            features = json.loads(path.read_bytes())
            branch_counts(features, receipt)
            rows.append(dict(case=name, repetition=rep, pid=pid, elf_sha256=case["elf_sha256"],
                             stdin_sha256=case["stdin_sha256"], stdout_sha256=case["expected_sha256"],
                             features=features, meter_jsonl=(records / f"process-{pid}.jsonl").read_text()))
            print(rep, name, features["cbr_executed"], features["mispredicts"], flush=True)
    evidence = dict(schema=1, protocol_sha256=digest(HERE / "protocol.json"), cpu=args.cpu,
                    tools_sha256={k: digest(v) for k, v in tools.items()},
                    probe_source_sha256=digest(EXPERIMENTS / "time_features/featclient.c"), rows=rows)
    (output / "records.json").write_text(json.dumps(evidence, indent=2) + "\n")


if __name__ == "__main__":
    main()
