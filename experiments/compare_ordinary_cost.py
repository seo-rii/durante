#!/usr/bin/env python3
"""Measure matched native, engine-only, ordinary and live v8 CPU costs on the three sealed CPP20 programs."""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import platform
from statistics import median
import struct
import subprocess

from collect_load_model import aggregate_records, require
from crossed_memory_model import EXTERNAL
from overhead import process_error, run_timed


RESULTS = Path(__file__).resolve().parent / "results/cpp20-crossed-memory"
ARMS = ("native", "engine", "ordinary", "live")
ROUNDS = 5
RUNTIME_OPTIONS = ["-quiet", "-no_cleancall_ignore_eflags", "-follow_children", "-disable_traces",
                   "-vm_size", "32M", "-vmheap_size", "32M"]
CLIENT_OPTIONS = ["-rep_bytes", "-cpuid_profile", "x86-64-v2"]


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def work(records, pid):
    receipt = aggregate_records(records, pid)
    require(receipt["complete"] and receipt["valid"] and receipt["quality"] == "uninterrupted" and
            receipt["cpuid_profile"] == "x86-64-v2" and receipt["rep_bytes_enabled"] and
            "latency_bound" not in receipt and receipt["unsupported_rep_entries"] == 0 and
            receipt["process_count"] == receipt["image_count"] == receipt["segments"][0]["threads"] == 1 and
            receipt["processes"][0]["exit_code"] == 0, "incompatible terminal work receipt")
    parts = receipt["rep_breakdown"]
    return dict(instructions=receipt["instructions"], rep_movs_bytes=parts["movs_bytes"], rep_stos_bytes=parts["stos_bytes"])


def measure(prefix, tools, folder, expected, slot, arm):
    run = slot / "current"
    run.mkdir()
    records, live = run / "records", run / "live"
    cmd = list(prefix)
    if arm != "native":
        cmd.extend([str(tools["drrun"]), *RUNTIME_OPTIONS])
        if arm in ("ordinary", "live"):
            records.mkdir()
            cmd.extend(["-c", str(tools["meter"]), "-outdir", str(records), *CLIENT_OPTIONS])
            if arm == "live":
                live.write_bytes(bytes(48))
                cmd.extend(["-live_work_v1", str(live)])
        cmd.append("--")
    with (folder / "in").open("rb") as stdin:
        row = run_timed([*cmd, str(folder / "Main")], 60, stdin=stdin)
    require(not process_error(row) and row["stdout"] == expected and not row["stderr"],
            f"{folder.name}/{arm}: status {row['exit_code']}, stderr {row['stderr'][-300:]!r}")
    counters = work(records, row["pid"]) if arm in ("ordinary", "live") else None
    if arm == "live":
        require(struct.unpack("<6Q", live.read_bytes()) ==
                (1, 0, counters["instructions"], counters["rep_movs_bytes"], counters["rep_stos_bytes"], 0),
                "live work differs from terminal receipt")
    return round(row["metrics"]["cpu_seconds"] * 1e9), counters


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("bundle", "corpus", "scratch", "output"):
        parser.add_argument("--" + key, required=True, type=lambda x: Path(x).resolve())
    parser.add_argument("--cpu", type=int, default=3)
    args = parser.parse_args()
    dr = args.bundle / "dynamorio"
    tools = {"drrun": dr / "bin64/drrun", "runtime": dr / "lib64/release/libdynamorio.so",
             "drmgr": dr / "ext/lib64/release/libdrmgr.so", "meter": args.bundle / "lib/libdurante.so"}
    feature_file = RESULTS / "features.json"
    seal = json.loads((RESULTS / "predictions.json").read_bytes())
    require(digest(feature_file) == seal["features_sha256"], "unexpected feature collection")
    provenance = json.loads(feature_file.read_bytes())["provenance"]
    hashes = {name: digest(path) for name, path in tools.items()}
    require(all(hashes[k] == provenance["local_sha256"][k] for k in hashes), "worker tool digest mismatch")
    manifest_path = args.corpus / "manifest.json"
    require(digest(manifest_path) == seal["manifest_sha256"], "unexpected corpus")
    manifest = json.loads(manifest_path.read_bytes())
    names = sorted(manifest["external"])
    require(set(names) == EXTERNAL, "unexpected external programs")
    os.sched_setaffinity(0, {args.cpu})
    args.scratch.mkdir(mode=0o700)
    os.chdir(args.scratch)
    prefix = ["env", "-i", "PATH=/usr/bin:/bin", "LC_ALL=C", f"DYNAMORIO_CONFIGDIR={args.scratch}",
              "setarch", "x86_64", "-R"]
    require(int(subprocess.check_output([*prefix, "cat", "/proc/self/personality"]), 16) & 0x40000,
            "fixed virtual address layout required")
    cases = {name: {"cpu_ns": {arm: [] for arm in ARMS}} for name in names}
    for n in range(ROUNDS):
        for pos in range(len(names)):
            index = (n + pos) % len(names)
            name = names[index]
            meta, folder = manifest["cases"][name], args.corpus / name
            ids = {key: digest(folder / file) for key, file in (("elf_sha256", "Main"), ("stdin_sha256", "in"),
                                                                ("stdout_sha256", "oracle"))}
            require(all(v == meta[k] for k, v in ids.items()), f"{name}: program, input or oracle differs")
            expected = (folder / "oracle").read_bytes()
            case = cases[name]
            case.update(ids)
            for i in range(len(ARMS)):
                arm = ARMS[(n + index + i) % len(ARMS)]
                cpu_ns, counts = measure(prefix, tools, folder, expected, args.scratch, arm)
                require(cpu_ns > 0, "missing wait4 CPU measurement")
                case["cpu_ns"][arm].append(cpu_ns)
                if counts is not None:
                    require(counts == case.setdefault("work", counts), f"{name}: terminal counters varied")
                (args.scratch / "current").rename(args.scratch / f"{name}-{n+1}-{arm}")
    for case in cases.values():
        times = case["cpu_ns"]
        case["median_cpu_ns"] = {k: median(v) for k, v in times.items()}
        case["median_paired_ratio_to_native"] = {
            arm: median(a/b for a,b in zip(times[arm], times["native"])) for arm in ARMS[1:]}
    require(hashes == {k: digest(v) for k, v in tools.items()}, "worker tools changed during collection")
    result = {"schema": 1, "manifest_sha256": seal["manifest_sha256"], "tools_sha256": hashes, "rounds": ROUNDS,
              "cpu": args.cpu, "kernel": platform.release(), "arm_order": ARMS, "runtime_options": RUNTIME_OPTIONS,
              "client_options": CLIENT_OPTIONS, "description": "Rotating arm and case order, fixed ASLR-disabled "
              "Linux CPU. wait4 child user plus system time includes startup. Engine-only runs without a DynamoRIO "
              "client. Live adds -live_work_v1 with a checked shared progress file.", "cases": cases}
    with args.output.open("x") as out:
        json.dump(result, out, indent=2, allow_nan=False)
        out.write("\n")


if __name__ == "__main__":
    main()
