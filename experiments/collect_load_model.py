#!/usr/bin/env python3
"""Collect paired load-model observations on the fixed Linux x86-64 CPP20 corpus."""
import argparse
import json
import os
from pathlib import Path
import platform
import random
import subprocess
import sys

from overhead import process_error, run_timed
from reference_features import count, require, sha
from time_features import digest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from collect import collect as aggregate_records

CORPUS = ROOT / "experiments/load_corpus.json"
FLAGS = ["-O2", "-Wall", "-lm", "--static", "-pipe", "-DONLINE_JUDGE=1", "-std=c++20"]
SEED = 20261118
REPEATS = 3


def prepare(args, corpus):
    args.output.mkdir(mode=0o700)
    for family, fixture in corpus["families"].items():
        folder = args.output / family
        folder.mkdir()
        source = folder / "Main.cpp"
        if "historical_source" in fixture:
            ref = corpus["aonohako_commit"] + ":" + fixture["historical_source"]
            source.write_bytes(subprocess.check_output(["git", "-C", args.aonohako, "show", ref]))
        else:
            source.write_bytes((ROOT / "experiments/time_features/heldout" / (family + ".cpp")).read_bytes())
        require(digest(source) == fixture["source_sha256"], f"{family}: source digest mismatch")
        subprocess.run(["docker", "run", "--rm", "--network", "none", "--user", f"{os.getuid()}:{os.getgid()}",
                        "-e", "LANG=C.UTF-8", "-e", "LC_ALL=C.UTF-8", "-v", f"{folder}:/work", "-w", "/work",
                        "--entrypoint", "/usr/bin/g++", corpus["compiler_image"], "Main.cpp", *FLAGS, "-o", "Main"],
                       check=True, timeout=90)
        require(digest(folder / "Main") == fixture["binary_sha256"], f"{family}: compiler artifact mismatch")


def checked_run(command, stdin, expected, timeout):
    with stdin.open("rb") as stream:
        result = run_timed(command, timeout, stdin=stream)
    error = process_error(result)
    require(not error, f"{Path(command[0]).name}: {error}, exit={result['exit_code']}, "
            f"stderr={result['stderr'][-500:]!r}")
    require(result["stdout"] == expected, f"{Path(command[-1]).name}: stdout mismatch")
    ns = round(result["metrics"]["cpu_seconds"] * 1_000_000_000)
    require(ns > 0, "missing child CPU usage")
    return result["pid"], ns


def quoted(path):
    # drrun tokenizes its -client option string independently of the shell.
    text = str(path)
    require(not any(c in text for c in ('"', "'", '`', '\n', '\r', ';')),
            "unsupported character in DynamoRIO options path")
    return f'"{text}"'


def run_with_meter(tools, program, stdin, expected, folder, probe_options, timeout, *, prefix=()):
    folder.mkdir(mode=0o700)
    records = folder / "records"
    records.mkdir(mode=0o700)
    opts = f"-outdir {quoted(records)} -rep_bytes -cpuid_profile x86-64-v2 -latency_bound"
    cmd = [tools["drrun"], "-quiet", "-client", tools["meter"], "00000000", opts]
    if probe_options is not None:
        cmd += ["-client", tools["probe"], "00000001", probe_options]
    cmd += ["-follow_children", "-disable_traces", "-vm_size", "32M", "-vmheap_size", "32M", "--", program]
    pid, cpu_ns = checked_run([*prefix, *cmd], stdin, expected, timeout)
    receipt = aggregate_records(records, pid)
    require(receipt.get("complete") is True and receipt["valid"] is True and
            receipt["cpuid_profile"] == "x86-64-v2" and receipt["quality"] == "uninterrupted" and
            receipt["image_count"] == receipt["process_count"] == 1 and
            receipt["processes"][0]["exit_code"] == 0 and receipt["segments"][0]["threads"] == 1 and
            receipt["rep_bytes_enabled"] is True and
            receipt["unsupported_rep_entries"] == 0 and receipt["latency_bound"]["version"] == 1,
            f"{program}: invalid production receipt: {receipt.get('reason', 'profile or accounting')}")
    return pid, cpu_ns, receipt


def metered(tools, program, stdin, expected, folder, enabled, timeout):
    probe_file = folder / "loads.json"
    opts = "-out " + quoted(probe_file) if enabled else None
    pid, cpu_ns, receipt = run_with_meter(tools, program, stdin, expected, folder, opts, timeout)
    counters = {k: receipt[k] for k in ("instructions", "rep_bytes")}
    if enabled:
        loads = json.loads(probe_file.read_bytes())
        require(type(loads["schema"]) is int and loads["schema"] == 1 and loads["pid"] == pid and
                type(loads["threads"]) is int and loads["threads"] == 1 and
                count(loads["instructions"]) and loads["instructions"] == counters["instructions"] and
                count(loads["load_instructions"]) and loads["load_instructions"] <= counters["instructions"],
                f"{program}: load probe and production receipt disagree")
        counters["load_instructions"] = loads["load_instructions"]
    return cpu_ns, counters


def collect(args, corpus, *, corpus_path=CORPUS, measure=metered, native_prefix=(),
            metadata=None, expected_tools=None):
    require(args.cpu in os.sched_getaffinity(0) and 1 <= args.timeout <= 600, "invalid CPU or timeout")
    core = args.bundle / "dynamorio"
    tools = {"drrun": core / "bin64/drrun", "runtime": core / "lib64/release/libdynamorio.so",
             "drmgr": core / "ext/lib64/release/libdrmgr.so", "meter": args.bundle / "lib/libdurante.so",
             "probe": args.probe}
    manifest = json.loads((core / ".durante-runtime.json").read_bytes())
    hashes = {name: digest(path) for name, path in tools.items()}
    if expected_tools:
        require(all(hashes[k] == v for k, v in expected_tools.items()), "judge installation differs from reference")
    require(manifest["artifacts"]["lib64/release/libdynamorio.so"] == hashes["runtime"] and
            manifest["inputs"]["patch_sha256"] == digest(ROOT / "toolkit/patches/dynamorio-vfork-lifetime.patch") and
            manifest["inputs"]["code_replay_patch_sha256"] == digest(ROOT / "toolkit/patches/dynamorio-code-replay.patch"),
            "patched DynamoRIO runtime mismatch")
    fixtures = {}
    for family, fixture in corpus["families"].items():
        folder = args.fixtures / family
        require(digest(folder / "Main") == fixture["binary_sha256"] and
                digest(folder / "Main.cpp") == fixture["source_sha256"], f"{family}: fixture digest mismatch")
        fixtures[family] = folder / "Main"
    args.output.mkdir(mode=0o700)
    result = {"schema": 1, "complete": False, "cpuid_profile": "x86-64-v2", "corpus_sha256": digest(corpus_path),
              "compiler_image": corpus["compiler_image"], "tools_sha256": hashes, "cpu": args.cpu,
              "kernel": platform.release(),
              "cpu_model": next((line.split(":", 1)[1].strip() for line in Path("/proc/cpuinfo").read_text().splitlines()
                                 if line.startswith("model name")), "unknown"),
              "order_seed": SEED, "native_clock": "direct-child wait4 user+system",
              "repetitions": REPEATS, "cases": [], **(metadata or {})}
    rows = {}
    for family, fixture in corpus["families"].items():
        for case in fixture["cases"]:
            scale = case["scale"]
            path = args.output / f"{family}-{scale}.stdin"
            path.write_bytes(case["stdin"].encode())
            row = {"family": family, "scale": scale, "input_sha256": digest(path),
                   "binary_sha256": fixture["binary_sha256"], "repetitions": []}
            rows[family, scale] = row, path, case["stdout"].encode()
            result["cases"].append(row)

    def checkpoint():
        part = args.output / "collection.tmp"
        part.write_text(json.dumps(result, indent=2) + "\n")
        part.replace(args.output / "collection.json")

    checkpoint()
    try:
        os.sched_setaffinity(0, {args.cpu})
        rng = random.Random(SEED)
        ordering = {key: i for i, key in enumerate(rows)}
        for rep in range(1, REPEATS + 1):
            keys = list(rows)
            rng.shuffle(keys)
            for family, scale in keys:
                row, stdin, expected = rows[family, scale]
                program = fixtures[family]
                _, before = checked_run([*native_prefix, program], stdin, expected, args.timeout)
                # Reverse each case's ordering across rounds and balance within each round.
                probe_first = (ordering[family, scale] + rep) % 2 == 0
                sample = {"repetition": rep, "native_before_ns": before, "probe_first": probe_first}
                for with_probe in (probe_first, not probe_first):
                    label = "probe" if with_probe else "meter"
                    folder = args.output / f"{family}-{scale}-{rep}-{label}"
                    cpu_ns, counters = measure(tools, program, stdin, expected, folder, with_probe, args.timeout)
                    sample[label + "_cpu_ns"] = cpu_ns
                    if with_probe:
                        sample.update(counters)
                    else:
                        sample["meter_only"] = counters
                _, sample["native_after_ns"] = checked_run([*native_prefix, program], stdin, expected, args.timeout)
                row["repetitions"].append(sample)
                checkpoint()
        require(hashes == {name: digest(path) for name, path in tools.items()}, "measurement tools changed")
        for family, path in fixtures.items():
            require(digest(path) == corpus["families"][family]["binary_sha256"], "fixture changed during collection")
        result["complete"] = True
    except Exception as err:
        result["failure"] = f"{family}/{scale}: {err}"
        raise
    finally:
        checkpoint()


def parse_args(description):
    parser = argparse.ArgumentParser(description=description)
    commands = parser.add_subparsers(dest="action", required=True)
    build = commands.add_parser("prepare")
    build.add_argument("--aonohako", type=Path, required=True)
    measure = commands.add_parser("collect")
    for opt in ("fixtures", "bundle", "probe"):
        measure.add_argument("--" + opt, required=True, type=lambda p: Path(p).resolve())
    measure.add_argument("--cpu", type=int, required=True)
    measure.add_argument("--timeout", type=int, default=120, help="per subprocess in seconds")
    for sub in (build, measure):
        sub.add_argument("--output", type=lambda p: Path(p).resolve(), required=True)
    return parser.parse_args()


def main():
    args = parse_args(__doc__)
    corpus = json.loads(CORPUS.read_bytes())
    try:
        if args.action == "prepare":
            prepare(args, corpus)
        else:
            collect(args, corpus)
    except (ValueError, OSError, subprocess.SubprocessError, KeyError) as err:
        parser.exit(1, str(err) + "\n")


if __name__ == "__main__":
    main()
