#!/usr/bin/env python3
"""Check translated sites, packed records and registered subsets against actual v11 live runs."""
import argparse
import json
from pathlib import Path
import runpy
import struct
import subprocess
import sys
import tempfile

from packed import decode_packed, HEADER, MAX_SITES, ROW

HERE = Path(__file__).resolve().parent
EXPERIMENTS = HERE.parents[1]
common = runpy.run_path(str(EXPERIMENTS / "live_sampler_cost/probe/check_controls.py"))
build, collect, native, offsets, quoted, require = (common[k] for k in ("build", "collect", "native", "offsets",
                                                                        "quoted", "require"))
COUNTS = ("loads", "pairs", "near_pairs", "far_pairs")


def run(args, folder, arm, binary, stdout, options="", rejected=False):
    folder.mkdir()
    records, clock, output = (folder / n for n in ("records", "clock", "sample"))
    records.mkdir()
    clock.write_bytes(bytes(48))
    primary = f"-outdir {quoted(records)} -rep_bytes -cpuid_profile x86-64-v2 -live_work_v1 {quoted(clock)}"
    secondary = args.span if arm == "full" else args.build / f"libsampler_{arm}.so"
    cmd = [args.drrun, "-quiet", "-client", args.meter, "00000000", primary, "-client", secondary, "00000001",
           f"-out {quoted(output)}{options}", "-follow_children", "-disable_traces", "-vm_size", "32M",
           "-vmheap_size", "32M", "--", binary]
    with subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as proc:
        actual, stderr = proc.communicate(timeout=30)
    if rejected:
        require(proc.returncode != 0 and not output.exists(), f"{folder}: accepted invalid profile")
        return
    require(proc.returncode == 0 and actual == stdout and not stderr, f"{folder}: {proc.returncode} {stderr!r}")
    m = collect(records, proc.pid)
    rep = m["rep_breakdown"]
    values = (1, 0, m["instructions"], rep["movs_bytes"], rep["stos_bytes"], 0)
    require(m["valid"] and m["complete"] and m["quality"] == "uninterrupted" and m["signals_observed"] == 0
            and m["cpuid_profile"] == "x86-64-v2" and struct.unpack("<6Q", clock.read_bytes()) == values,
            f"{folder}: invalid live clock or measurement")
    raw = output.read_bytes()
    report = decode_packed(raw) if arm == "packed" else json.loads(raw)
    require(report.pop("pid") == proc.pid, f"{folder}: wrong owner")
    return report, values


def check(args, folder, binary, selected, expected=None, expected_count=None):
    folder.mkdir()
    stdout = native(binary)
    full, reference = run(args, folder / "full", "full", binary, stdout)
    packed, clock = run(args, folder / "packed", "packed", binary, stdout)
    require(clock == reference and packed == full, f"{folder}: packed changed original telemetry")
    if expected is not None:
        symbols = offsets(binary)
        names = (*COUNTS, "sampled_address_span_bytes")
        rows = [{"offset": symbols[s], **dict(zip(names, v, strict=True))} for s, v in expected.items()]
        require(full["sites"] == sorted(rows, key=lambda x: x["offset"]), f"{folder}: hand-counted sites differ")
    if expected_count is not None:
        require(len(full["sites"]) == expected_count, f"{folder}: bad number of sites")
    catalog, clock = run(args, folder / "catalog", "catalog", binary, stdout)
    require(clock == reference and set(catalog) == {"schema", "arm", "threads", "sites"} and
            (catalog["schema"], catalog["arm"], catalog["threads"]) == (1, "catalog", 1) and
            catalog["sites"] == sorted(set(catalog["sites"])) and
            all(s["offset"] in catalog["sites"] for s in full["sites"]), f"{folder}: incomplete translation catalog")
    options = " -sites " + ",".join(str(i) for i in selected)
    profiled, clock = run(args, folder / "profiled", "profiled", binary, stdout, options)
    rows = [s for s in full["sites"] if s["offset"] in selected]
    expected_profile = {**full, "sites": rows, **{k: sum(s[k] for s in rows) for k in COUNTS}}
    require(clock == reference and profiled == expected_profile, f"{folder}: wrong selected-site projection")
    return (folder / "packed/sample").read_bytes()


def check_packed_errors(raw):
    values = list(HEADER.unpack_from(raw))
    invalid = [b"", raw[:HEADER.size - 1], raw[:-1], raw + b"x", bytes(HEADER.size + MAX_SITES * ROW.size + 1)]
    for index, value in ((0, b"DSPAN002"), (1, 1), (2, 0), (3, 2), (4, 512), (5, 128), (10, MAX_SITES + 1)):
        changed = values.copy()
        changed[index] = value
        invalid.append(HEADER.pack(*changed) + raw[HEADER.size:])
    for data in invalid:
        try:
            decode_packed(data)
        except ValueError:
            continue
        raise ValueError("decoder accepted invalid wire data")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("drrun", "meter", "span", "build"):
        parser.add_argument("--" + name, required=True, type=lambda value: Path(value).resolve())
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as folder:
        tmp = Path(folder)
        for n, near, far, span in ((0, 0, 0, 0), (1, 0, 0, 0), (2, 1, 0, 64), (1024, 1, 0, 64),
                                   (1025, 1, 0, 128), (1026, 1, 1, 192)):
            binary = build(tmp, EXPERIMENTS / "cpp20_locality/probe/span_control.S", f"-DVISITS={n}", pie=True)
            expected = {"walk_site": (n, near + far, near, far, span)} if n else {}
            check(args, tmp / f"phase-{n}", binary, [offsets(binary)["walk_site"]], expected)

        binary = build(tmp, EXPERIMENTS / "access_sample/control.S")
        syms = offsets(binary)
        selected = sorted((syms["near_site"], syms["once_site"]))
        raw = check(args, tmp / "subset", binary, selected,
                    {"near_site": (2050, 3, 3, 0, 64), "far_site": (2050, 3, 0, 3, 128),
                     "once_site": (1, 0, 0, 0, 0)})
        check_packed_errors(raw)
        check(args, tmp / "unexecuted", binary, [0])
        for i, options in enumerate(("", ' -sites ""', " -sites 1,", " -sites 01", " -sites -1", " -sites 3,2",
                                     " -sites 1,1", " -sites 0xff", " -sites 18446744073709551616",
                                     " -sites " + ",".join(map(str, range(65))))):
            run(args, tmp / f"invalid-{i}", "profiled", binary, b"", options, rejected=True)

        source = tmp / "many.S"
        source.write_text(".global _start\n.text\n_start: lea word(%rip), %rsi\n.rept N\n"
                          "mov (%rsi), %eax\n.endr\nmov %eax,%edi\nmov $60,%eax\nsyscall\n.data\nword: .long 0\n")
        for n in (31, 32, 33, 64, 65):
            binary = build(tmp, source, f"-DN={n}")
            check(args, tmp / f"chunk-{n}", binary, [0], expected_count=n)
    print("Verified catalog coverage, lossless chunked encoding, selected-site execution and input rejections.")


if __name__ == "__main__":
    main()
