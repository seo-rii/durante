#!/usr/bin/env python3
"""Check partial EFLAGS dependencies against a production v1 latency client."""
import argparse
import json
from pathlib import Path
import re
import runpy
import subprocess
from tempfile import TemporaryDirectory

from fixtures import loop_listing
from study import HERE, ROOT, require

collect = runpy.run_path(str(ROOT / "collect.py"))["collect"]


def reference_bound_x16(preserve_carry):
    """Schedule the five loop instructions with separate CF and ZF dependencies."""
    carry_ready = counter_ready = 0
    finish = []
    for _ in range(8):
        # MOV-immediate has no input and is ready at cycle one on each pass.
        adc_ready = max(1, carry_ready) + 1
        product_ready = adc_ready + 3
        counter_ready += 1
        carry_ready = product_ready if preserve_carry else counter_ready
        # JNZ consumes the countdown's ZF. Its result feeds no register.
        finish.append(max(product_ready, counter_ready + 1))
    return max(5 * 4, (finish[7] - finish[3]) * 4)


def measure(sdk, client, compiler, work, countdown, iterations):
    preserve = countdown == "dec"
    binary = work / f"{countdown}-{iterations}"
    subprocess.run([compiler, "-nostdlib", "-static", "-no-pie", "-Wl,--build-id=none",
                    f"-DITERATIONS={iterations}", f"-DPRESERVE_CARRY={int(preserve)}",
                    str(HERE / "carry_control.S"), "-o", binary], check=True)
    loop = loop_listing(binary)
    require(loop[:4] == ["mov $0x2aaaaaaa,%eax", "adc $0x0,%eax", "imul $0x3,%eax,%eax",
                         "dec %ecx" if preserve else "sub $0x1,%ecx"] and len(loop) == 5
            and re.fullmatch(r"jne [0-9a-f]+ <hot_begin>", loop[4]),
            f"{binary.name}: unexpected loop {loop}")
    expected = b"\x01" if preserve else b"\xfe"
    require(subprocess.check_output([binary], timeout=10) == expected,
            f"{binary.name}: native checksum mismatch")
    records = work / (binary.name + "-records")
    records.mkdir(mode=0o700)
    cmd = [sdk / "bin64/drrun", "-root", sdk, "-quiet", "-disable_traces",
           "-c", client, "-outdir", records, "-rep_bytes", "-latency_bound", "--", binary]
    with subprocess.Popen(cmd, stdout=subprocess.PIPE) as process:
        try:
            stdout, _ = process.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise
    require(process.returncode == 0 and stdout == expected, f"{binary.name}: metered checksum mismatch")
    report = collect(records, process.pid)
    require(report["valid"] and report["complete"] and report["quality"] == "uninterrupted",
            f"{binary.name}: invalid measurement {report.get('reason')}")
    # Two setup instructions, five per iteration, nine for write and exit.
    instructions = 11 + 5 * iterations
    require(report["instructions"] == instructions and report["rep_bytes_enabled"]
            and report["rep_entries"] == report["unsupported_rep_entries"] == 0,
            f"{binary.name}: instruction or REP count mismatch")
    require(report.get("latency_bound") == {"version": 1, "cycles_x16": 4 * instructions},
            f"{binary.name}: unexpected v1 bound {report.get('latency_bound')}")
    return {"iterations": iterations, "stdout_hex": stdout.hex(), "instructions": instructions,
            "v1_cycles_x16": report["latency_bound"]["cycles_x16"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk", type=Path, required=True, help="prepared DynamoRIO root with bin64/drrun")
    parser.add_argument("--client", type=Path, required=True, help="production libdurante.so")
    parser.add_argument("--compiler", default="cc")
    args = parser.parse_args()
    results = {}
    with TemporaryDirectory(prefix="durante-carry-") as tmp:
        for name, expected in (("dec", 64), ("sub", 20)):
            reference = reference_bound_x16(name == "dec")
            require(reference == expected, f"{name}: per-flag reference disagrees with hand schedule")
            rows = [measure(args.sdk.resolve(), args.client.resolve(), args.compiler, Path(tmp), name, n)
                    for n in (100, 1000)]
            delta = rows[1]["v1_cycles_x16"] - rows[0]["v1_cycles_x16"]
            count = rows[1]["iterations"] - rows[0]["iterations"]
            require(delta == 20 * count, f"{name}: v1 iteration cost changed")
            results[name] = {"runs": rows, "v1_x16_per_iteration": delta // count,
                             "per_flag_reference_x16_per_iteration": reference}
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
