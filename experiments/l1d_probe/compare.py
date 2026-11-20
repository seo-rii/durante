#!/usr/bin/env python3
"""Time two L1D diagnostic clients on identical, trusted, single-threaded x86-64 programs."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import statistics
import struct
import subprocess
import time


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def clocks(output):
    reports = list(output.glob("process-*.jsonl"))
    if len(reports) != 1:
        raise ValueError("expected one process report")
    start, end = map(json.loads, reports[0].read_bytes().splitlines())
    if (start["event"] != "start" or start["pid"] != end["pid"] or
            start["segment"] != end["segment"] or end["event"] != "end" or
            not start["rep_bytes_enabled"] or not end["valid"] or not end["threads_complete"] or
            end["quality"] != "uninterrupted" or end["accounting_error"] or
            not end["app_exit_seen"] or end["app_exit_code"] != 0 or end["signals_observed"] != 0 or
            end["threads_started"] != 1 or end["threads_finished"] != 1):
        raise ValueError("incomplete, interrupted or multithreaded execution")
    words = struct.unpack("<6Q", (output / "live").read_bytes())
    if words != (1, 0, end["instructions"], end["rep_movs_bytes"], end["rep_stos_bytes"], 0):
        raise ValueError("live clock and terminal work counters differ")
    return {key: value for key, value in end.items() if key not in ("pid", "segment")}


def measure(drrun, case, slot, client, cpu, timeout, probe):
    name, program, stdin_file, expected = case
    output = slot / "run"
    output.mkdir()
    (output / "live").write_bytes(bytes(48))
    # The same paths and environment keep even the program's initial stack comparable.
    installed = slot / "client.so"
    shutil.copyfile(client, installed)
    command = [
        "timeout", "--kill-after=2s", f"{timeout}s",
        "setarch", "x86_64", "-R", "taskset", "-c", str(cpu), str(drrun),
        "-quiet", "-no_cleancall_ignore_eflags", "-follow_children", "-disable_traces",
        "-vm_size", "32M", "-vmheap_size", "32M", "-c", str(installed),
        "-outdir", str(output), "-rep_bytes", "-cpuid_profile", "x86-64-v2",
        *(["-probe_l1d"] if probe else []),
        "-live_work_v1", str(output / "live"), "--", str(program),
    ]
    env = {"PATH": os.defpath, "LC_ALL": "C", "DYNAMORIO_CONFIGDIR": str(slot)}
    with (output / "stdout").open("wb") as stdout, (output / "stderr").open("wb") as stderr, stdin_file.open("rb") as stdin:
        start = time.monotonic_ns()
        with subprocess.Popen(command, stdin=stdin, stdout=stdout, stderr=stderr, cwd=slot, env=env) as process:
            _, status, usage = os.wait4(process.pid, 0)
            process.returncode = os.waitstatus_to_exitcode(status)
        wall_ns = time.monotonic_ns() - start
    if process.returncode != 0:
        raise ValueError(f"{name} exited {process.returncode}, see {output / 'stderr'}")
    if digest(output / "stdout") != expected:
        raise ValueError(f"{name} produced incorrect stdout")

    pattern = rb"^PROBE_L1D refs=(\d+) lines=(\d+) misses=(\d+) skipped=(\d+) rep=(\d+) threads=1$"
    stderr = (output / "stderr").read_bytes()
    if b"Option parsing error" in stderr:
        raise ValueError(f"runtime rejected an instrumentation option, see {output / 'stderr'}")
    matches = re.findall(pattern, stderr, re.MULTILINE)
    if len(matches) != int(probe):
        raise ValueError(f"unexpected number of single-thread L1D reports: {len(matches)}")
    cache = dict(zip(("refs", "lines", "misses", "skipped", "rep"), map(int, matches[0]))) if probe else None
    return {"cpu_s": usage.ru_utime + usage.ru_stime, "wall_s": wall_ns / 1e9,
            "work": clocks(output), "cache": cache}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("drrun", "baseline", "candidate", "output"):
        parser.add_argument("--" + option, type=Path, required=True)
    parser.add_argument("--case", nargs=4, action="append", required=True,
                        metavar=("NAME", "PROGRAM", "STDIN", "EXPECTED_STDOUT"))
    parser.add_argument("--cpu", type=int, required=True)
    parser.add_argument("--rounds", type=int, default=3)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--include-off", action="store_true",
                        help="also time the candidate with its cache probe disabled")
    modes.add_argument("--ordinary-live", action="store_true",
                       help="compare two ordinary live clients with cache instrumentation disabled")
    parser.add_argument("--timeout", type=int, default=150, help="seconds per execution")
    args = parser.parse_args()
    if args.rounds < 1 or args.timeout < 1 or args.cpu not in os.sched_getaffinity(0):
        parser.error("positive rounds and timeout, and an available logical CPU, are required")
    if len({c[0] for c in args.case}) != len(args.case):
        parser.error("case names must be unique")

    os.umask(0o077)
    root = args.output.resolve()
    root.mkdir()
    slot = root / "slot"
    slot.mkdir()
    # Snapshot caller inputs before any timing, so rebuilding or editing them cannot mix results.
    images = {}
    report = {"cpu": args.cpu, "rounds": args.rounds, "clients": {}, "cases": {}, "results": {}}
    for mode, source in (("baseline", args.baseline), ("candidate", args.candidate)):
        images[mode] = root / (mode + ".so")
        shutil.copyfile(source, images[mode])
        report["clients"][mode] = digest(images[mode])
    if args.ordinary_live:
        report["mode"] = "ordinary-live"
    if args.include_off:
        images["off"] = images["candidate"]
        report["clients"]["off"] = report["clients"]["candidate"]
    drrun = args.drrun.resolve(strict=True)
    report["drrun_sha256"] = digest(drrun)
    cases = []
    for index, (name, binary, source, oracle) in enumerate(args.case):
        program, stdin = root / f"target-{index}", root / f"input-{index}"
        shutil.copy(binary, program)
        shutil.copyfile(source, stdin)
        expected = digest(Path(oracle))
        cases.append((name, program, stdin, expected))
        report["cases"][name] = {"elf_sha256": digest(program), "stdin_sha256": digest(stdin),
                                 "stdout_sha256": expected}

    work_reference, cache_reference = {}, {}
    rows = []
    modes = list(images)
    for i in range(args.rounds):
        for j, case in enumerate(cases):
            offset = (i + j) % len(modes)
            order = modes[offset:] + modes[:offset]
            for mode in order:
                probe = not args.ordinary_live and mode != "off"
                sample = measure(drrun, case, slot, images[mode], args.cpu, args.timeout, probe)
                archive = root / f"sample-{i + 1}-{j + 1}-{mode}"
                (slot / "run").rename(archive)
                (archive / "sample.json").write_text(json.dumps(sample, indent=2) + "\n")
                if work_reference.setdefault(case[0], sample["work"]) != sample["work"]:
                    raise ValueError(f"{case[0]} work counters differ, see {archive}")
                if probe and cache_reference.setdefault(case[0], sample["cache"]) != sample["cache"]:
                    raise ValueError(f"{case[0]} cache counters differ, see {archive}")
                rows.append({"name": case[0], "mode": mode, "round": i + 1, **sample})
                print(f"{case[0]} {mode} #{i + 1}: {sample['cpu_s']:.3f} CPU seconds", flush=True)

    for name, *_ in cases:
        result = {}
        for mode in images:
            samples = [r for r in rows if r["name"] == name and r["mode"] == mode]
            result[mode] = {}
            for key in ("cpu_s", "wall_s"):
                values = [r[key] for r in samples]
                result[mode][key] = {"median": statistics.median(values), "min": min(values), "max": max(values)}
        report["results"][name] = result
    report["samples"] = rows
    (root / "summary.json").write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
