"""Run the neutral hand oracle through the standalone trip probe on Linux/x86-64."""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile

from receipt import validate


def kernel_symbols(program):
    symbols = {}
    for line in subprocess.check_output(["nm", "-n", str(program)], text=True).splitlines():
        fields = line.split()
        if len(fields) == 3:
            symbols[fields[2]] = int(fields[0], 16)
    return symbols


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("drrun", type=Path)
    parser.add_argument("probe", type=Path)
    args = parser.parse_args()
    here = Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory() as work:
        directory = Path(work)
        program = directory / "smoke"
        subprocess.run(["cc", "-O2", "-Wall", "-Wextra", "-Werror", "-no-pie",
                        "-Wl,-Ttext-segment=0x400000", here / "check.c", here / "check.S",
                        "-o", program], check=True)
        symbols = kernel_symbols(program)
        for mode, names, expected in (
            ("closed", ("fallthrough", "split"), (7, 4, 2, 2, 0)),
            ("partial", ("partial",), (3, 0, 0, 0, 3)),
        ):
            arguments = [] if mode == "closed" else ["--partial"]
            subprocess.run([program, *arguments], check=True)
            receipt = directory / f"{mode}.json"
            command = [args.drrun.resolve(), "-quiet", "-client", args.probe.resolve(),
                       "00000001", f"-out {receipt}", "-disable_traces", "--", program, *arguments]
            with subprocess.Popen(command) as child:
                if child.wait() != 0:
                    raise RuntimeError(f"failed {mode} execution")
            data = validate(json.loads(receipt.read_bytes()), child.pid)
            sites = {site["branch_offset"]: site for site in data["sites"]}
            if set(sites) != {symbols[f"{name}_branch"] - 0x400000 for name in names}:
                raise AssertionError(data)
            for name in names:
                site = sites[symbols[f"{name}_branch"] - 0x400000]
                fields = ("visits", "completed", "changes", "last_length", "pending_length")
                if (tuple(site[k] for k in fields) != expected
                        or site["target_offset"] != symbols[f"{name}_header"] - 0x400000):
                    raise AssertionError(site)
            print(f"{mode}: {len(sites)} verified site(s), counters {expected}")


if __name__ == "__main__":
    main()
