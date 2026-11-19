#!/usr/bin/env python3
"""Check the diagnostic client against the fixed memory-access trace in control.S."""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile

from compare import clocks


# Columns: references, cache lines touched, misses (32 KiB, 8-way LRU).
# 128 identical loads                  128  128  1
# same-set LRU sequence                 12   12  9
# cross-line store, RMW, indexed read    4    8  1
# initialize source byte                 1    1  1
# 66 descending MOVSB iterations       132  132  0
# 3 forward STOSW iterations             3    4  2
# verify four stored values              4    5  0
# scratch register and flag checks       4    4  1
# XLAT with two address sizes            2    2  0
EXPECTED_CACHE = b"PROBE_L1D refs=290 lines=296 misses=15 skipped=0 rep=2 threads=1"
EXPECTED_WORK = {
    "instructions": 128 + 8 * 4 + 47 + 20 + 10,
    "rep_bytes": 72, "rep_entries": 2, "unsupported_rep_entries": 0,
    "rep_movs_bytes": 66, "rep_movs_entries": 1,
    "rep_stos_bytes": 6, "rep_stos_entries": 1,
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("drrun", "client", "program"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as temporary:
        output = Path(temporary).resolve()
        live = output / "live"
        live.write_bytes(bytes(48))
        command = [
            args.drrun.resolve(strict=True), "-quiet", "-no_cleancall_ignore_eflags",
            "-follow_children", "-disable_traces", "-vm_size", "32M", "-vmheap_size", "32M",
            "-c", args.client.resolve(strict=True), "-outdir", output, "-rep_bytes",
            "-cpuid_profile", "x86-64-v2", "-probe_l1d", "-live_work_v1", live,
            "--", args.program.resolve(strict=True),
        ]
        result = subprocess.run(command, cwd=output, stdin=subprocess.DEVNULL, capture_output=True,
                                env={"LC_ALL": "C", "PATH": os.defpath, "DYNAMORIO_CONFIGDIR": str(output)},
                                timeout=20)
        if result.returncode != 0:
            raise SystemExit(f"control exited {result.returncode}\n{result.stderr.decode(errors='replace')}")
        if b"Option parsing error" in result.stderr:
            raise SystemExit(result.stderr.decode(errors="replace"))
        cache = [line for line in result.stderr.splitlines() if line.startswith(b"PROBE_L1D ")]
        if cache != [EXPECTED_CACHE]:
            raise SystemExit(f"expected {EXPECTED_CACHE!r}, got {cache!r}")
        work = clocks(output)
        actual = {key: work[key] for key in EXPECTED_WORK}
        if actual != EXPECTED_WORK:
            raise SystemExit(f"expected {EXPECTED_WORK}, got {actual}")


if __name__ == "__main__":
    main()
