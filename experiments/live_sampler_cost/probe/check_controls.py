#!/usr/bin/env python3
"""Compare live clocks, original records, and hand-counted sampler fixtures across all five metered arms."""
import argparse
import json
from pathlib import Path
import runpy
import struct
import subprocess
import sys
from tempfile import TemporaryDirectory

HERE = Path(__file__).resolve().parent
EXPERIMENTS = HERE.parents[1]
REPO = EXPERIMENTS.parent
sys.path.insert(0, str(EXPERIMENTS / "access_sample"))
from check_control import build, native, offsets, quoted

collect = runpy.run_path(str(REPO / "collect.py"))["collect"]
ARMS = ("live", "empty", "gate", "full", "fused")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def trial(args, folder, arm, binary, stdout):
    folder.mkdir()
    records, clock, sample = (folder / s for s in ("records", "clock", "sample.json"))
    records.mkdir()
    clock.write_bytes(bytes(48))
    opts = f"-outdir {quoted(records)} -rep_bytes -cpuid_profile x86-64-v2 -live_work_v1 {quoted(clock)}"
    client = args.meter
    if arm == "fused":
        client = args.build / "libsampler_fused.so"
        opts += " -span_report " + quoted(sample)
    command = [args.drrun, "-quiet", "-client", client, "00000000", opts]
    if arm in ("empty", "gate", "full"):
        second = args.span if arm == "full" else args.build / f"libsampler_{arm}.so"
        command += ["-client", second, "00000001", "-out " + quoted(sample)]
    command += ["-follow_children", "-disable_traces", "-vm_size", "32M", "-vmheap_size", "32M", "--", binary]
    with subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as proc:
        actual_stdout, stderr = proc.communicate(timeout=30)
    require(proc.returncode == 0 and actual_stdout == stdout and not stderr, f"{folder}: {proc.returncode} {stderr!r}")
    raw = collect(records, proc.pid)
    require(raw["valid"] and raw["complete"] and raw["quality"] == "uninterrupted" and raw["signals_observed"] == 0
            and raw["cpuid_profile"] == "x86-64-v2" and raw["unsupported_rep_entries"] == 0
            and raw["process_count"] == raw["image_count"] == raw["segments"][0]["threads"] == 1,
            f"{folder}: v11 receipt")
    rep = raw["rep_breakdown"]
    terminal = (1, 0, raw["instructions"], rep["movs_bytes"], rep["stos_bytes"], 0)
    require(struct.unpack("<6Q", clock.read_bytes()) == terminal and terminal[2] > 0, f"{folder}: terminal budget")
    if arm == "live":
        require(not sample.exists(), f"{folder}: unexpected sidecar")
        return terminal, None
    receipt = json.loads(sample.read_bytes())
    require(receipt.pop("pid") == proc.pid and receipt["threads"] == 1, f"{folder}: sampler identity")
    return terminal, receipt


def check(args, tmp, tag, source, expected=None, traffic=(0, 0), flags=(), pie=False):
    binary = build(tmp, source, *flags, pie=pie)
    stdout = native(binary)
    runs = {arm: trial(args, tmp / f"{tag}-{arm}", arm, binary, stdout) for arm in ARMS}
    clock = runs["live"][0]
    require(clock[3:5] == traffic and all(result[0] == clock for result in runs.values()), f"{tag}: live drift")
    require(runs["empty"][1] == {"schema": 1, "arm": "empty", "threads": 1}, f"{tag}: empty control")
    full = runs["full"][1]
    require(set(full) == {"schema", "threads", "period", "line_bytes", "loads", "pairs", "near_pairs", "far_pairs", "sites"}
            and full["schema"] == 2 and full["period"] == 1024 and full["line_bytes"] == 64, f"{tag}: full schema")
    require(runs["fused"][1] == full, f"{tag}: fused differs from the registered full sampler")
    if expected is not None:
        symbols = offsets(binary)
        names = ("loads", "pairs", "near_pairs", "far_pairs", "sampled_address_span_bytes")
        rows = [{"offset": symbols[s], **dict(zip(names, v, strict=True))} for s, v in expected.items()]
        require(full["sites"] == sorted(rows, key=lambda row: row["offset"]), f"{tag}: hand-counted sites")
    sites = [{k: row[k] for k in ("offset", "loads", "pairs")} for row in full["sites"]]
    gate = {"schema": 1, "threads": 1, "arm": "gate", "period": 1024,
            "loads": sum(s["loads"] for s in sites), "pairs": sum(s["pairs"] for s in sites), "sites": sites}
    require(runs["gate"][1] == gate, f"{tag}: gate cadence")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("drrun", "meter", "span", "build"):
        parser.add_argument("--" + name, required=True, type=lambda value: Path(value).resolve())
    args = parser.parse_args()
    with TemporaryDirectory() as folder:
        tmp = Path(folder)
        for n, near, far, span in ((0, 0, 0, 0), (1, 0, 0, 0), (2, 1, 0, 64), (1024, 1, 0, 64),
                                   (1025, 1, 0, 128), (1026, 1, 1, 192)):
            check(args, tmp, f"phase-{n}", EXPERIMENTS / "cpp20_locality/probe/span_control.S",
                  {"walk_site": (n, near+far, near, far, span)} if n else {},
                  flags=[f"-DVISITS={n}"], pie=True)
        check(args, tmp, "flags", EXPERIMENTS / "access_sample/control.S",
              {"near_site": (2050, 3, 3, 0, 64), "far_site": (2050, 3, 0, 3, 128), "once_site": (1, 0, 0, 0, 0)})
        check(args, tmp, "stack", EXPERIMENTS / "access_sample/address_control.S",
              {name: (2050, 3, 0, 3, 68) for name in ("stack_site", "scaled_site")})
        check(args, tmp, "mixed-rep", REPO / "tests/rep_breakdown.S", {}, traffic=(65, 66))
        check(args, tmp, "xmm-cpuid", REPO / "tests/latency_bound/memory_operand_bytes.S", traffic=(7, 4))
    print("Verified the same live clocks and registered sampler cadence across 50 runs.")


if __name__ == "__main__":
    main()
