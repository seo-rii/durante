"""Check sampled index bounds, the inherited pair stream and original application state."""

import argparse
import json
from pathlib import Path
import runpy
import struct
import subprocess
import tempfile

from receipt import PERIOD, validate

HERE = Path(__file__).resolve().parent
PREVIOUS = HERE.parents[1] / "search_order/probe"
hand = runpy.run_path(str(PREVIOUS / "check.py"))


def compile_hand(source, program):
    subprocess.run(
        [
            "cc",
            "-nostdlib",
            "-no-pie",
            "-static",
            "-Wl,--build-id=none",
            "-Wl,-Ttext-segment=0x400000",
            source,
            "-o",
            program,
        ],
        check=True,
    )
    return {
        fields[2]: int(fields[0], 16) - 0x400000
        for line in subprocess.check_output(
            ["nm", "-n", program], text=True
        ).splitlines()
        if len(fields := line.split()) == 3
    }


def report_for(drrun, probe, program, n, offset, destination, expected):
    pid, status, out, err = hand["invoke"](
        program, n, drrun, probe, hex(offset), destination
    )
    if status or err or out != expected:
        raise ValueError(
            f"incorrect application state: status {status}, stderr {err!r}"
        )
    receipt = json.loads(destination.read_bytes())
    if receipt["pid"] != pid:
        raise ValueError("wrong PID in live receipt")
    return receipt


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("drrun", type=Path)
    cli.add_argument("probe", type=Path)
    cli.add_argument(
        "--v1", type=Path, help="also require live parity for every original counter"
    )
    args = cli.parse_args()
    indices = hand["INDICES"]
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        program = root / "hand"
        symbols = compile_hand(PREVIOUS / "check.S", program)
        pc = symbols["terminal"]
        for n in (
            0,
            1,
            2,
            PERIOD,
            PERIOD + 1,
            PERIOD + 2,
            2 * PERIOD + 2,
            4 * PERIOD + 1,
        ):
            expected = struct.pack(
                "<QQ",
                sum(indices[i % 4] + (i & 1) for i in range(n)),
                0x3456789ABCDEF102,
            )
            args_for = (program, n, pc)
            receipt = report_for(
                args.drrun, args.probe, *args_for, root / f"sample-{n}.json", expected
            )
            validate(receipt, receipt["pid"])
            selected = [indices[i % 4] for i in range(n) if i % PERIOD < 2]
            bounds = (min(selected), max(selected)) if selected else (None, None)
            histogram = [0] * 61
            for a, b in zip(selected[::2], selected[1::2]):
                histogram[((a >> 4) ^ (b >> 4)).bit_length()] += 1
            if (
                (receipt["sampled_min_index"], receipt["sampled_max_index"]) != bounds
                or receipt["visits"] != n
                or receipt["pc"] != pc
                or receipt["suffix_histogram"] != histogram
            ):
                raise ValueError(f"wrong selection or counter at {n} visits")
            if args.v1:
                old = report_for(
                    args.drrun,
                    args.v1,
                    *args_for,
                    root / f"original-{n}.json",
                    expected,
                )
                original_fields = set(old) - {"pid", "schema"}
                if any(old[k] != receipt[k] for k in original_fields):
                    raise ValueError("historical counters differ")
        for invalid in ("nothex", hex(symbols["bad_site"]), "0xffffffffffffffff"):
            output = root / f"invalid-{invalid}.json"
            _, status, _, _ = hand["invoke"](
                program, 2, args.drrun, args.probe, invalid, output
            )
            if not status or output.exists():
                raise ValueError("accepted invalid terminal offset")

        pending = root / "pending"
        offset = compile_hand(HERE / "pending.S", pending)["terminal"]
        receipt = report_for(
            args.drrun, args.probe, pending, 1022, offset, root / "pending.json", b""
        )
        validate(receipt, receipt["pid"])
        if (
            receipt["sampled_min_index"],
            receipt["sampled_max_index"],
            receipt["visits"],
            receipt["pairs"],
        ) != (3, 208, 1022, 1):
            raise ValueError(
                "pending endpoint did not extend the completed pair (3,20)"
            )
        for lo, hi in [
            (True, 208),
            (3, False),
            (None, 208),
            (20, 3),
            (-1, 208),
            (3, 2**64),
            (3, 3),
        ]:
            bad = {**receipt, "sampled_min_index": lo, "sampled_max_index": hi}
            try:
                validate(bad, receipt["pid"])
            except ValueError:
                continue
            raise ValueError("invalid sampled bounds were accepted")
    print(
        "Verified empty, single, complete and pending samples; terminal CMP/ADC, SIMD state and invalid registrations."
    )


if __name__ == "__main__":
    main()
