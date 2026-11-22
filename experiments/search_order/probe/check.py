"""Check the live DynamoRIO terminal sampler at and across the sampling boundaries."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import struct
import subprocess
import tempfile

from receipt import PERIOD, validate

INDICES = [16, 127, 128, 0]


def invoke(program, n, drrun, probe, offset, path):
    command = [drrun, "-quiet", "-client", probe, "00000001", f'-pc {offset} -out "{path}"',
               "-disable_traces", "--", program]
    with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as child:
        out, err = child.communicate(struct.pack("<Q", n), timeout=30)
        return child.pid, child.returncode, out, err


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("drrun", type=Path)
    cli.add_argument("probe", type=Path)
    args = cli.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        program = directory / "hand"
        subprocess.run(["cc", "-nostdlib", "-no-pie", "-static", "-Wl,--build-id=none",
                        "-Wl,-Ttext-segment=0x400000", Path(__file__).with_name("check.S"), "-o", program], check=True)
        symbols = {}
        for line in subprocess.check_output(["nm", "-n", program], text=True).splitlines():
            value, _, name = line.split()
            symbols[name] = int(value, 16) - 0x400000
        pc = symbols["terminal"]
        for n in (0, 1, 2, PERIOD, PERIOD + 1, PERIOD + 2, 2 * PERIOD + 2, 4 * PERIOD + 1):
            output = directory / f"{n}.json"
            pid, code, out, err = invoke(program, n, args.drrun, args.probe, hex(pc), output)
            expected = sum(INDICES[i % 4] + (i & 1) for i in range(n))
            if code or err or out != struct.pack("<QQ", expected, 0x3456789ABCDEF102):
                raise ValueError(f"incorrect application state at {n} visits: exit {code}, stderr {err!r}")
            report = validate(json.loads(output.read_bytes()), pid)
            histogram = [0] * 61
            for i in range(0, n - 1, PERIOD):
                a, b = (INDICES[j % 4] >> 4 for j in (i, i + 1))
                histogram[(a ^ b).bit_length()] += 1
            if report["pc"] != pc or report["visits"] != n or report["suffix_histogram"] != histogram:
                raise ValueError(f"wrong terminal readback: {report}")
        for invalid in ("nothex", hex(symbols["bad_site"]), "0xffffffffffffffff"):
            path = directory / f"bad-{invalid}.json"
            _, code, _, _ = invoke(program, 2, args.drrun, args.probe, invalid, path)
            if code == 0 or path.exists():
                raise ValueError(f"accepted bad registered site {invalid}")
        for field, value in (("pairs", True), ("visits", 0), ("suffix_sum", 100), ("suffix_histogram", [])):
            wrong = deepcopy(report)
            wrong[field] = value
            try:
                validate(wrong, report["pid"])
            except ValueError:
                continue
            raise ValueError(f"accepted malformed {field}")
    print("Verified eight hand-counted schedules, CMP/ADC and SIMD state, rejected invalid registrations and reports.")


if __name__ == "__main__":
    main()
