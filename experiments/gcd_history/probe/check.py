"""Check both register/flag paths, cold and aliased histories, and partial traversal."""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile

from receipt import validate


HERE = Path(__file__).resolve().parent
V1 = HERE.parents[1] / "gcd_controls/trip_probe"
FIELDS = ("visits", "completed", "changes", "last_length", "pending_length", "unpredicted_changes")

# Sequence, expected unpredicted changes. Each runs at two interleaved static sites.
CASES = (
    ((1,), 0),
    ((2, 2, 2, 2), 0),
    ((1, 2) * 5, 2),
    ((1, 1, 2, 2) * 4, 2),
    ((7, 7, 27, 27) * 4, 2),   # Both repeated odd-length pairs retain distinct slots.
    ((1, 1, 2, 1, 1, 2), 2),  # The second-order prediction rescues the last 2.
    ((1, 2, 1, 1, 2), 3),     # Repeating 1 replaces the old singleton successor 2.
    ((1, 2, 65, 2), 3),       # Keys 1 and 65 collide in the first table.
    ((1, 1, 11, 14, 11), 3),   # (1, 1) and (11, 14) collide at second-table slot 7.
    ((2, 3, 2, 3, 3, 5), 3),
)


def run_client(launcher, dso, program, args, output):
    cmd = [launcher, "-quiet", "-client", dso, "00000001", f"-out {output}",
           "-disable_traces", "--", program, *args]
    with subprocess.Popen(cmd) as child:
        if child.wait() != 0:
            raise RuntimeError(f"failed hand fixture {args}")
    return json.loads(output.read_bytes()), child.pid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("drrun", type=Path)
    parser.add_argument("probe", type=Path)
    parser.add_argument("--v1", type=Path, help="also compare every historical field with the original DSO")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        program = root / "hand"
        subprocess.run(["cc", "-O2", "-Wall", "-Wextra", "-Werror", "-no-pie",
                        "-Wl,-Ttext-segment=0x400000", HERE / "check.c", HERE / "partial.S",
                        V1 / "check.S", "-o", program], check=True)
        listing = subprocess.check_output(["nm", "-n", program], text=True)
        symbols = {fields[2]: int(fields[0], 16) - 0x400000 for line in listing.splitlines()
                   if len(fields := line.split()) == 3}
        fixtures = []
        for sequence, misses in CASES:
            changes = sum(a != b for a, b in zip(sequence, sequence[1:]))
            fixtures.append((list(map(str, sequence)), ("fallthrough", "split"),
                             (sum(sequence), len(sequence), changes, sequence[-1], 0, misses)))
        fixtures.append((["--partial"], ("mixed",), (10, 5, 4, 1, 3, 2)))
        for i, (arguments, kernels, expected) in enumerate(fixtures):
            subprocess.run([program, *arguments], check=True)
            report, pid = run_client(args.drrun, args.probe, program, arguments, root / f"{i}.json")
            validate(report, pid)
            sites = {site["branch_offset"]: site for site in report["sites"]}
            if set(sites) != {symbols[name + "_branch"] for name in kernels}:
                raise AssertionError(report)
            for name in kernels:
                site = sites[symbols[name + "_branch"]]
                if (site["target_offset"] != symbols[name + "_header"]
                        or tuple(site[k] for k in FIELDS) != expected):
                    raise AssertionError((arguments, expected, site))
            if args.v1:
                prior, _ = run_client(args.drrun, args.v1, program, arguments, root / f"{i}-v1.json")
                projected = [{k: v for k, v in s.items() if k != "unpredicted_changes"}
                             for s in report["sites"]]
                if prior["schema"] != 1 or prior["sites"] != projected:
                    raise AssertionError((prior, projected))
        print(f"Verified {len(fixtures)} live history cases, both flag paths and an interrupted traversal.")


if __name__ == "__main__":
    main()
