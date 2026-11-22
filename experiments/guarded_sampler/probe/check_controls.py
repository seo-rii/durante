#!/usr/bin/env python3
"""Compare the guard with hand counts, real v11 records and the unchanged full sampler."""
import argparse
import hashlib
import json
from pathlib import Path
import runpy
import struct
import subprocess
import tarfile
import tempfile

HERE = Path(__file__).resolve().parent
EXPERIMENTS = HERE.parents[1]
common = runpy.run_path(str(EXPERIMENTS / "live_sampler_cost/probe/check_controls.py"))
build, collect, offsets, quoted, require = (common[key] for key in ("build", "collect", "offsets", "quoted", "require"))
COUNTS = ("loads", "pairs", "near_pairs", "far_pairs")


def trial(args, root, binary, arm, selected, stdin=b"", stdout=b"", reject=False):
    root.mkdir()
    records, clock, sample, guard = (root / s for s in ("records", "clock", "sample.json", "guard.json"))
    records.mkdir()
    clock.write_bytes(bytes(48))
    base = f"-outdir {quoted(records)} -rep_bytes -cpuid_profile x86-64-v2 -live_work_v1 {quoted(clock)}"
    second = f"-out {quoted(sample)}"
    if arm == "guarded":
        second += " -sites " + ",".join(map(str, selected)) + " -guard " + quoted(guard)
    command = [args.drrun, "-quiet", "-client", args.meter, "00000000", base, "-client",
               args.guarded if arm == "guarded" else args.span, "00000001", second,
               "-follow_children", "-disable_traces", "-vm_size", "32M", "-vmheap_size", "32M", "--", binary]
    with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as proc:
        actual, stderr = proc.communicate(stdin, timeout=30)
    if reject:
        require(proc.returncode != 0 and b"interrupted execution" in stderr and
                not guard.read_bytes() and not sample.read_bytes(), f"{root}: accepted interrupted run {stderr!r}")
        return
    require(proc.returncode == 0 and actual == stdout and not stderr, f"{root}: exit {proc.returncode} {stderr!r}")
    receipt = collect(records, proc.pid)
    rep = receipt["rep_breakdown"]
    terminal = (1, 0, receipt["instructions"], rep["movs_bytes"], rep["stos_bytes"], 0)
    require(receipt["complete"] and receipt["valid"] and receipt["quality"] == "uninterrupted" and
            receipt["signals_observed"] == 0 and receipt["cpuid_profile"] == "x86-64-v2" and
            tuple(struct.unpack("<6Q", clock.read_bytes())) == terminal, f"{root}: invalid production clock")
    report = json.loads(sample.read_bytes())
    require(report.pop("pid") == proc.pid, f"{root}: sample PID")
    omitted = None
    if arm == "guarded":
        sidecar = json.loads(guard.read_bytes())
        omitted = sidecar.pop("omitted_loads")
        require(type(omitted) is int and 0 <= omitted <= 2**64 - 1 and
                sidecar == {"schema": 1, "arm": "guarded", "scope": "main_module_eligible_scalar_reads",
                            "pid": proc.pid, "threads": 1}, f"{root}: guard receipt")
    return report, omitted, terminal


def check(args, root, binary, selected, total, omitted, *, stdin=b"", stdout=b"", rows=None):
    root.mkdir()
    native = subprocess.run([binary], input=stdin, capture_output=True, check=True)
    require(native.stdout == stdout and not native.stderr, f"{root}: native result")
    full, _, baseline = trial(args, root / "full", binary, "full", selected, stdin, stdout)
    sparse, guard, clock = trial(args, root / "guarded", binary, "guarded", selected, stdin, stdout)
    if rows is not None:
        require(full["sites"] == rows, f"{root}: full differs from independent oracle")
    wanted = [r for r in full["sites"] if r["offset"] in selected]
    projected = {**full, "sites": wanted, **{k: sum(r[k] for r in wanted) for k in COUNTS}}
    require(clock == baseline and sparse == projected and full["loads"] == total and
            guard == omitted == full["loads"] - sparse["loads"], f"{root}: invalid guard or selected projection")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("drrun", "meter", "span", "guarded"):
        parser.add_argument("--" + name, required=True, type=lambda s: Path(s).resolve())
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as name:
        tmp = Path(name)
        for n in (1, 63, 64, 65):
            binary = build(tmp, HERE / "guard_control.S", f"-DOMITTED={n}", pie=True)
            total = 3 * (n + 2)
            check(args, tmp / f"group-{n}", binary, [offsets(binary)["selected_site"]], total, total - 3)
        binary = build(tmp, EXPERIMENTS / "access_sample/control.S")
        syms = offsets(binary)
        for name, selected, omitted in (("all", sorted(syms.values()), 0), ("none", [0], 4101),
                                       ("mixed", [syms["near_site"]], 2051)):
            check(args, tmp / name, binary, selected, 4101, omitted)
        binary = build(tmp, EXPERIMENTS / "cpp20_locality/probe/span_control.S", "-DVISITS=0", pie=True)
        check(args, tmp / "empty", binary, [0], 0, 0, rows=[])
        binary = tmp / "path_mix"
        control = HERE.parent / "control"
        with tarfile.open(control / "elf.tar.xz") as archive:
            binary.write_bytes(archive.extractfile("bin/path_mix").read())
        binary.chmod(0o700)
        for record in json.loads((control / "cases.json").read_bytes()):
            require(hashlib.sha256(binary.read_bytes()).hexdigest() == record["binary_sha256"], "path_mix ELF digest")
            first, second = record["mix_oracle"]
            check(args, tmp / record["split"], binary, [first["offset"]], first["loads"] + second["loads"],
                  second["loads"], stdin=record["stdin"].encode(), stdout=record["stdout"].encode(),
                  rows=record["mix_oracle"])
        # An ordinary delivered and handled application signal invalidates the guard.
        signal = tmp / "signal"
        source = b"#include <signal.h>\nstatic void handle(int n) {(void)n;}\nint main(void) {" \
                 b"signal(SIGUSR1, handle); return raise(SIGUSR1);}\n"
        subprocess.run(["cc", "-x", "c", "-static", "-", "-o", signal], input=source, check=True)
        trial(args, tmp / "interrupted", signal, "guarded", [0], reject=True)
        math = tmp / "math"
        subprocess.run(["cc", "-std=c17", "-Wall", "-Wextra", "-Werror", HERE / "check_math.c", "-o", math], check=True)
        subprocess.run([math], check=True)
    print("Verified per-group counts, sparse records, flags, full clock, interrupted run and overflow bounds.")


if __name__ == "__main__":
    main()
