#!/usr/bin/env python3
"""Replay two previously measured algorithms under the original CPP20 judge profile."""
import argparse
from fractions import Fraction
import json
import os
from pathlib import Path
import platform
import subprocess

from collect_load_model import CORPUS, ROOT, quoted, run_with_meter
from load_model import distribution
from reference_features import CONTROL_OUTPUT, check_controls, count, observation, require, sha
from time_features import digest


REFERENCE = ROOT / "experiments/results/cpp20-load-model.json"
REFERENCE_SHA256 = "dd62734d0690c2b0fbfd8df09fb483f9834fc50b0bf5ee16e5f22b8a3a29d2d8"
FAMILIES = ("lower_bound", "window_sum")
SCALES = (1, 2, 4)


def compare(search, window):
    # Min search and max window loosen the original median-based lower bound.
    ns, nw = search["native_cpu_ns"], window["native_cpu_ns"]
    bounds = {"median": Fraction(2 * distribution(ns)["median"], 3 * distribution(nw)["median"]),
              "fastest_search_slowest_window": Fraction(2 * min(ns), 3 * max(nw))}
    features = {}
    for key in search["observations"][0]:
        s = [r[key] for r in search["observations"]]
        w = [r[key] for r in window["observations"]]
        row = {"search": [min(s), max(s)], "window": [min(w), max(w)]}
        for name, bound in bounds.items():
            if min(s) > 0 and min(s) * bound.denominator >= max(w) * bound.numerator:
                row[name] = "spans"
            elif max(s) == 0 or max(s) * bound.denominator < min(w) * bound.numerator:
                row[name] = "insufficient"
            else:
                row[name] = "overlap"
        features[key] = row
    return {"required_search_to_window": {k: str(v) for k, v in bounds.items()}, "features": features}


def read_pattern(raw, meter):
    require(count(raw["instructions"]) and raw["instructions"] > 0 and
            raw["instructions"] == meter["instructions"], "same-run instruction totals disagree")
    require(meter["authority"] == "diagnostic_only" and meter["integrity_verified"] is False and
            meter["measurement_contract"]["counter_integrity"] == "application_mutable" and
            meter["measurement_contract"]["trusted_verdict_eligible"] is False,
            "unexpected production counter contract")
    return {"instructions": meter["instructions"], "rep_bytes": meter["rep_bytes"],
            "latency_x16": meter["latency_bound"]["cycles_x16"], **observation(raw)}


def collect(args):
    require(args.cpu in os.sched_getaffinity(0) and 1 <= args.timeout <= 600, "invalid CPU or timeout")
    ref_bytes = REFERENCE.read_bytes()
    require(sha(ref_bytes) == REFERENCE_SHA256, "reference collection differs from the published study")
    ref = json.loads(ref_bytes)
    require(digest(CORPUS) == ref["corpus_sha256"], "corpus digest differs from the reference")
    corpus = json.loads(CORPUS.read_bytes())
    core = args.bundle / "dynamorio"
    ext = core / "ext/lib64/release"
    tools = {"drrun": core / "bin64/drrun", "runtime": core / "lib64/release/libdynamorio.so",
             "meter": args.bundle / "lib/libdurante.so", "drmgr": ext / "libdrmgr.so",
             "probe": args.probe, "control": args.control}
    hashes = {k: digest(v) for k, v in tools.items()}
    for key in ("drrun", "runtime", "meter", "drmgr"):
        require(hashes[key] == ref["tools_sha256"][key], f"{key} differs from the #53 judge installation")
    personality = subprocess.check_output(["setarch", "-R", "cat", "/proc/self/personality"],
                                          text=True, timeout=10).strip()
    require(int(personality, 16) & 0x40000, "ADDR_NO_RANDOMIZE unavailable")
    args.output.mkdir(mode=0o700)
    os.sched_setaffinity(0, {args.cpu})
    result = {"schema": 1, "complete": False, "reference_sha256": REFERENCE_SHA256,
              "corpus_sha256": ref["corpus_sha256"], "tools_sha256": hashes,
              "cpuid_profile": "x86-64-v2", "counter_integrity": "application_mutable",
              "integrity_verified": False, "personality_hex": personality,
              "host_randomize_va_space": Path("/proc/sys/kernel/randomize_va_space").read_text().strip(),
              "cpu": args.cpu, "kernel": platform.release(), "controls": {}, "cases": {}}

    def checkpoint():
        tmp = args.output / "patterns.tmp"
        tmp.write_text(json.dumps(result, indent=2) + "\n")
        tmp.replace(args.output / "patterns.json")

    def measure(label, program, input_bytes, expected_bytes, repeats, row):
        stdin = args.output / (label + ".stdin")
        stdin.write_bytes(input_bytes)
        row.update(input_sha256=sha(input_bytes), expected_sha256=sha(expected_bytes), observations=[])
        for i in range(repeats):
            folder = args.output / f"{label}-{i + 1}"
            raw = folder / "patterns.json"
            opts = f"-out {quoted(raw)} -bpred -load_pattern"
            _, _, meter = run_with_meter(tools, program, stdin, expected_bytes, folder, opts, args.timeout,
                                         prefix=("setarch", "-R"))
            row["observations"].append(read_pattern(json.loads(raw.read_bytes()), meter))
            checkpoint()

    try:
        for mode, expected in enumerate(CONTROL_OUTPUT):
            row = {}
            result["controls"][str(mode)] = row
            measure(f"control-{mode}", args.control, f"{mode}\n".encode(), (expected+"\n").encode(), 1, row)
        check_controls(result["controls"])
        for family in FAMILIES:
            fixture = corpus["families"][family]
            folder = args.fixtures / family
            program = folder / "Main"
            require(digest(folder / "Main.cpp") == fixture["source_sha256"] and
                    digest(program) == fixture["binary_sha256"], f"{family}: fixture identity mismatch")
            for case in fixture["cases"]:
                scale = case["scale"]
                refcase, = [r for r in ref["cases"] if r["family"] == family and r["scale"] == scale]
                stdin = case["stdin"].encode()
                require(sha(stdin) == refcase["input_sha256"] and
                        refcase["binary_sha256"] == fixture["binary_sha256"], "reference case mismatch")
                label = f"{family}-{scale}"
                native = [r[k] for r in refcase["repetitions"] for k in ("native_before_ns", "native_after_ns")]
                row = {"binary_sha256": fixture["binary_sha256"], "native_cpu_ns": native}
                result["cases"][label] = row
                measure(label, program, stdin, case["stdout"].encode(), 3, row)
            require(digest(program) == fixture["binary_sha256"], f"{family}: executable changed during replay")
        require(hashes == {k: digest(v) for k, v in tools.items()}, "tool or control changed during replay")
        result["comparisons"] = {
            f"search-{s}-window-{w}": compare(result["cases"][f"lower_bound-{s}"],
                                             result["cases"][f"window_sum-{w}"])
            for s, w in [(4, 1), *((scale, scale) for scale in SCALES)]}
        result["complete"] = True
    finally:
        checkpoint()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("bundle", "fixtures", "probe", "control", "output"):
        parser.add_argument("--" + option, required=True, type=lambda p: Path(p).resolve())
    parser.add_argument("--cpu", type=int, required=True)
    parser.add_argument("--timeout", type=int, default=180, help="seconds per process")
    args = parser.parse_args()
    try:
        collect(args)
    except (KeyError, TypeError, ValueError, OSError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"CPP20 pattern replay failed: {exc}\n")
    print(args.output / "patterns.json")


if __name__ == "__main__":
    main()
