#!/usr/bin/env python3
"""Replay the two CPP20 branch/sort conflicts from aonohako PR 121."""

import argparse
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys

from time_features import cache_stats, digest


PR121_DATASET_SHA256 = "d1fd7c1f03e541a75ff45fef104a9e3af9de16956995c514634ec187ccde11d7"

# These two comparisons share algorithms; the second checks another scale.
PAIRS = ((("branch", 1), ("sort", 2)), (("branch", 2), ("sort", 4)))
CONTROL_OUTPUT = ("00000aaac2aa6000", "0000080022aa6000",
                  "00000aaabaa71979", "000008001aa71979")
PATTERNS = ("first", "same_line", "adjacent_line", "nonadjacent_line")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def count(value):
    return type(value) is int and 0 <= value < 2**64


def reference_case(dataset, fixtures, name, scale):
    rows = [row for row in dataset["samples"]
            if (row["workload_id"], row["scale"]) == (name, scale)]
    repetitions = dataset["repetitions"]
    require(type(repetitions) is int and 3 <= repetitions <= 9 and
            sorted(row["repetition"] for row in rows) == list(range(1, repetitions + 1)),
            f"{name}/{scale}: incomplete or duplicate reference repetitions")
    files = fixtures / name
    binary = files / "Main"
    stdin = (files / f"{scale}.stdin").read_bytes()
    hashes = {"binary_sha256": digest(binary), "source_sha256": digest(files / "Main.cpp"),
              "input_sha256": sha(stdin), "expected_sha256": rows[0]["expected_sha256"]}
    cpu = []
    baseline = []
    for row in rows:
        require(row["split"] == "validation" and all(row[k] == v for k, v in hashes.items()),
                f"{name}/{scale}: artifact, source, input or oracle mismatch")
        for key in ("native_before", "native_after"):
            native = row[key]
            ns = native["cpu_time_ns"]
            require(native["status"] == "Accepted" and native["exit_code"] == 0 and
                    native["verdict_source"] == "stdout" and count(ns) and ns > 0 and
                    not native.get("cpu_time_normalization") and not native.get("cpu_time_sampling"),
                    f"{name}/{scale}: missing native CPU reference")
            cpu.append(ns)
        measured = row["metered"]
        execution = measured["work_measurement_execution"]
        r = measured["work_measurement"]
        require(execution["state"] == "complete" and execution["output_matches"] is True and
                measured["exit_code"] == 0 and r["complete"] is True and r["quality"] == "uninterrupted"
                and r["cpuid_profile"] == dataset["cpuid_profile"] and r["family"] == "cpp"
                and r["processes"] == r["images"] == 1 and r["latency_bound"]["version"] == 1,
                f"{name}/{scale}: incomplete baseline receipt")
        counters = {"instructions": r["instructions"], "rep_bytes": r["rep_bytes"],
                    "latency_x16": r["latency_bound"]["cycles_x16"]}
        require(all(count(v) for v in counters.values()) and counters["instructions"] > 0 and
                counters["latency_x16"] >= 4 * counters["instructions"], "invalid baseline counters")
        baseline.append(counters)
    cpu.sort()
    median = (cpu[repetitions - 1] + cpu[repetitions] + 1) // 2
    return binary, stdin, {**hashes, "native_cpu_ns": cpu, "native_median_ns": median,
                           "baseline": baseline, "observations": []}


def run_checked(command, input_bytes, expected_sha, timeout):
    # drcachesim may launch a separate tracer. Bound the whole process group.
    with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, start_new_session=True) as child:
        try:
            stdout, stderr = child.communicate(input_bytes, timeout=timeout)
        except BaseException:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.communicate()
            raise
    require(child.returncode == 0, f"{command[0]} exited {child.returncode}: {stderr[-1024:]!r}")
    require(sha(stdout) == expected_sha, "stdout does not match the independent checksum oracle")
    return stderr


def observation(raw):
    p = raw["load_pattern"]
    require(raw["bpred"] is True and count(raw["cond_branches"]) and
            raw["cbr_executed"] == raw["cond_branches"] and
            count(raw["cbr_executed"]) and count(raw["mispredicts"]) and
            raw["mispredicts"] <= raw["cbr_executed"], "invalid gshare receipt")
    require(type(p["version"]) is int and p["version"] == 1 and set(p) == {"version", *PATTERNS} and
            all(count(p[k]) for k in PATTERNS) and count(raw["loads"]) and
            sum(p[k] for k in PATTERNS) <= raw["loads"], "invalid scalar load-pattern receipt")
    return {"gshare_mispredicts": raw["mispredicts"], "conditional_branches": raw["cbr_executed"],
            "load_instructions": raw["loads"], **{k: p[k] for k in PATTERNS}}


def check_controls(controls):
    for mode in range(4):
        # One input-byte load and one 32,768-word sweep from an aligned array.
        expected = (2, 0, 0, 32767) if mode & 1 else (2, 30720, 2047, 0)
        for obs in controls[str(mode)]["observations"]:
            require(tuple(obs[k] for k in PATTERNS) == expected,
                    f"control {mode}: incorrect per-site line counts")
    for a, b in ((0, 2), (1, 3)):
        low = max(o["gshare_mispredicts"] for o in controls[str(a)]["observations"])
        high = min(o["gshare_mispredicts"] for o in controls[str(b)]["observations"])
        require(high > low, f"control {b}: branch ordering did not increase gshare misses")


def compare(branch, sort):
    # At ±20%, the smallest acceptable sort time divided by the largest
    # acceptable branch time is (4/5 * sort median) / (6/5 * branch median).
    ratio = Fraction(2 * sort["native_median_ns"], 3 * branch["native_median_ns"])
    features = {}
    for group in ("baseline", "observations", "cache"):
        if group not in branch and group not in sort:
            continue
        for key in branch[group][0]:
            b = [r[key] for r in branch[group]]
            s = [r[key] for r in sort[group]]
            if min(s) > 0 and min(s) * ratio.denominator >= max(b) * ratio.numerator:
                result = "spans"
            elif max(s) == 0 or max(s) * ratio.denominator < min(b) * ratio.numerator:
                result = "insufficient"
            else:
                result = "overlap"
            features[key] = {"branch_range": [min(b), max(b)], "sort_range": [min(s), max(s)],
                             "result": result}
    return {"required_sort_to_branch_ratio": str(ratio), "features": features}


def collect(args):
    data = args.dataset.read_bytes()
    dataset_sha = sha(data)
    require(dataset_sha == args.dataset_sha256, "reference dataset SHA-256 mismatch")
    dataset = json.loads(data)
    require(dataset["schema"] == 1 and dataset["complete"] is True and dataset["smoke"] is False and
            not dataset.get("failures") and dataset["corpus_id"] == "cpp-reference-v1" and
            dataset["source_language"] == "CPP20" and dataset["cpuid_profile"] == "x86-64-v2" and
            dataset["latency_bound_version"] == 1, "expected a complete fixed-CPUID CPP20 dataset")
    require(2 <= args.repeats <= 9 and 1 <= args.timeout <= 600, "invalid repeat count or timeout")
    require(args.cpu in os.sched_getaffinity(0), "selected CPU is unavailable")
    tools = {"client": args.client, "drrun": args.sdk / "bin64/drrun",
             "core": args.sdk / "lib64/release/libdynamorio.so", "control": args.control}
    tools["drmgr"] = args.sdk / "ext/lib64/release/libdrmgr.so"
    if args.cachesim_root:
        root = args.cachesim_root
        tools.update({"cache_drrun": root / "bin64/drrun", "cache_core": root / "lib64/release/libdynamorio.so",
                      "cache_launcher": root / "tools/bin64/drmemtrace_launcher",
                      "cache_tracer": root / "tools/lib64/release/libdrmemtrace.so",
                      "cache_config": root / "tools/drcachesim.drrun64"})
    receipt = {"schema": 1, "complete": False, "dataset_sha256": dataset_sha,
               "reference_runtime_image": dataset["runtime_image"],
               "reference_cpuid_profile": dataset["cpuid_profile"], "replay_cpu": args.cpu,
               "kernel": platform.release(), "repeats": args.repeats,
               "tools_sha256": {k: digest(p) for k, p in tools.items()}, "controls": {}, "cases": {}}
    args.output.mkdir(mode=0o700)
    path = args.output / "results.json"

    def checkpoint():
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(receipt, indent=2) + "\n")
        tmp.replace(path)

    def measure(label, binary, stdin, row, cache):
        for i in range(args.repeats):
            raw_path = args.output / f"{label}-{i + 1}.json"
            run_checked(["setarch", "-R", tools["drrun"], "-quiet", "-disable_traces", "-c", args.client,
                         "-out", raw_path, "-bpred", "-load_pattern", "--", binary],
                        stdin, row["expected_sha256"], args.timeout)
            row["observations"].append(observation(json.loads(raw_path.read_bytes())))
            checkpoint()
        if cache:
            stderr = run_checked(["setarch", "-R", tools["cache_drrun"], "-t", "drcachesim", "--", binary],
                                 stdin, row["expected_sha256"], args.timeout)
            (args.output / f"{label}-cache.txt").write_bytes(stderr)
            stats = cache_stats(stderr.decode())
            row["cache"] = [{"l1d_demand_misses": stats["l1d"]["misses"],
                             "ll_demand_misses": stats["ll"]["misses"]}]
            checkpoint()

    checkpoint()
    os.sched_setaffinity(0, {args.cpu})
    try:
        for mode, expected in enumerate(CONTROL_OUTPUT):
            stdin = f"{mode}\n".encode()
            controls = receipt["controls"]
            controls[str(mode)] = {"input_sha256": sha(stdin), "expected_sha256": sha((expected + "\n").encode()),
                                   "observations": []}
            measure(f"control-{mode}", args.control, stdin, controls[str(mode)], False)
        check_controls(controls)
        for pair in PAIRS:
            for name, scale in pair:
                binary, stdin, row = reference_case(dataset, args.fixtures, name, scale)
                label = f"{name}-{scale}"
                receipt["cases"][label] = row
                measure(label, binary, stdin, row, bool(args.cachesim_root))
                require(digest(binary) == row["binary_sha256"], f"{label}: executable changed during replay")
        require(receipt["tools_sha256"] == {k: digest(p) for k, p in tools.items()},
                "a replay tool or control executable changed during collection")
        receipt["comparisons"] = {
            f"branch-{b}-sort-{s}": compare(receipt["cases"][f"branch-{b}"], receipt["cases"][f"sort-{s}"])
            for (_, b), (_, s) in PAIRS}
        receipt["complete"] = True
    except (KeyError, TypeError, ValueError, OSError, subprocess.SubprocessError, IndexError) as err:
        receipt["failure"] = str(err)
        raise
    finally:
        checkpoint()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("dataset", "fixtures", "sdk", "client", "control", "output"):
        parser.add_argument("--" + name, type=lambda p: Path(p).resolve(), required=True)
    parser.add_argument("--dataset-sha256", default=PR121_DATASET_SHA256,
                        help="expected digest of a validated reference archive (default: aonohako PR 121)")
    parser.add_argument("--cachesim-root", type=lambda p: Path(p).resolve())
    parser.add_argument("--cpu", type=int, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=180, help="seconds per subprocess, maximum 600")
    args = parser.parse_args()
    try:
        collect(args)
    except (KeyError, TypeError, ValueError, OSError, subprocess.SubprocessError, IndexError) as err:
        parser.exit(1, f"reference feature replay failed: {err}\n")
    print(args.output / "results.json")


if __name__ == "__main__":
    main()
