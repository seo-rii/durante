#!/usr/bin/env python3
"""Collect paired enabled/disabled cache diagnostics with fixed application arguments."""
import argparse
import json
import os
from pathlib import Path
import platform
import re

from l1d_probe.compare import clocks, digest
from overhead import process_error, run_timed
from reference_features import require

CACHE_LINE = re.compile(rb"PROBE_L1D refs=(\d+) lines=(\d+) misses=(\d+) skipped=(\d+) rep=(\d+) threads=1\n")


def collect(args):
    sdk = args.drrun.parents[1]
    inputs = {"drrun": args.drrun, "client": args.client, "runtime": sdk / "lib64/release/libdynamorio.so",
              "drmgr": sdk / "ext/lib64/release/libdrmgr.so", "elf": args.program,
              "input": args.stdin, "output": args.oracle}
    hashes = {name: digest(f) for name, f in inputs.items()}
    expected = args.oracle.read_bytes()
    args.output.mkdir(mode=0o700)
    args.config.mkdir(mode=0o700, exist_ok=True)
    os.sched_setaffinity(0, {args.cpu})
    prefix = ["env", "-i", "PATH=/usr/bin:/bin", "LC_ALL=C", f"DYNAMORIO_CONFIGDIR={args.config}",
              "setarch", "x86_64", "-R", str(args.drrun), "-quiet", "-no_cleancall_ignore_eflags",
              "-follow_children", "-disable_traces", "-vm_size", "32M", "-vmheap_size", "32M",
              "-c", str(args.client)]
    reference = None
    rows = []
    for i in range(args.pairs):
        sample = {"repetition": i + 1}
        for mode in (("off", "on") if i % 2 == 0 else ("on", "off")):
            folder = args.output / f"{i + 1}-{mode}"
            folder.mkdir(mode=0o700)
            live = folder / "live"
            live.write_bytes(bytes(48))
            command = [*prefix, "-outdir", str(folder), "-rep_bytes", "-cpuid_profile", "x86-64-v2",
                       "-live_work_v1", str(live)]
            if mode == "on":
                command.append("-probe_l1d")
            with args.stdin.open("rb") as stdin:
                row = run_timed([*command, "--", str(args.program)], args.timeout, stdin=stdin)
            if mode == "on":
                report = CACHE_LINE.fullmatch(row["stderr"])
                require(report is not None, f"{folder.name}: invalid cache report (exit {row['exit_code']}, {row['stderr'][-500:]!r})")
                sample["cache"] = dict(zip(("refs", "lines", "misses", "skipped", "rep"), map(int, report.groups())))
                row["stderr"] = b""
            require(not process_error(row) and row["stdout"] == expected, f"{folder.name}: execution or stdout mismatch")
            work = clocks(folder)
            require(work["unsupported_rep_entries"] == 0, f"{folder.name}: unsupported REP")
            if reference is None:
                reference = work
            require(reference == work, f"{folder.name}: instrumented work changed")
            sample[mode + "_cpu_ns"] = round(row["metrics"]["cpu_seconds"] * 1e9)
        rows.append(sample)
    require(all(digest(f) == hashes[k] for k, f in inputs.items()), "an input changed during measurement")
    result = {"schema": 1, "sha256": hashes, "cpu": args.cpu, "kernel": platform.release(),
              "work": {k: reference[k] for k in ("instructions", "rep_movs_bytes", "rep_stos_bytes")},
              "samples": rows}
    (args.output / "cache.json").write_text(json.dumps(result, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("drrun", "client", "program", "stdin", "oracle", "config", "output"):
        parser.add_argument("--" + key, required=True, type=lambda p: Path(p).resolve())
    parser.add_argument("--cpu", type=int, required=True)
    parser.add_argument("--pairs", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=240)
    args = parser.parse_args()
    if args.pairs < 1 or args.timeout < 1 or args.cpu not in os.sched_getaffinity(0):
        parser.error("positive pairs and timeout and an available CPU are required")
    collect(args)


if __name__ == "__main__":
    main()
