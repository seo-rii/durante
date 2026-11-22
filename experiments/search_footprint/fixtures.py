"""Reconstruct the twelve uniform Rust lower-bound inputs and exact endpoint oracles."""
import argparse
import json
from pathlib import Path
import runpy
import subprocess

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PREVIOUS = ROOT / "experiments/search_order/fixtures.py"
SOURCE = runpy.run_path(str(PREVIOUS))
FIRST_MANIFEST = ROOT / "experiments/results/gce-search-order/fixtures.json"
MANIFEST = ROOT / "experiments/results/gce-search-footprint/fixtures.json"

# The archived inputs reuse exact source data. Every other row is (keys, queries, seed).
CASES = {
    "fit_16k": (4096, 3145728, 314159271),
    "fit_256k": (65536, 1572864, 828182847),
    "fit_512k": (131072, 1310720, 459045235),
    "fit_1m": (262144, 1048576, 360287471),
    "fit_8m": (2097152, 1048576, 135266249),
    "archived": "rust_original",
    "prior_nonpower": "rust_new",
    "new_96k": (24571, 2097152, 846264338),
    "new_384k": (98317, 1572864, 327950289),
    "new_768k": (196613, 1310720, 419716941),
    "new_2m": (524309, 1048576, 974944592),
    "new_4m": (1048559, 1048576, 307816406),
}


def sample_span(keys, targets):
    """Replay both ends of each sampled pair, including a final unmatched first visit."""
    period = SOURCE["PERIOD"]
    minimum, maximum, count = keys, -1, 0
    for first in range(0, len(targets), period):
        for target in targets[first:first + 2]:
            terminal, _ = SOURCE["replay_one"](keys, target)
            minimum = min(minimum, terminal)
            maximum = max(maximum, terminal)
            count += 1
    if count == 0:
        raise ValueError("nonempty queries required for footprint calibration")
    return {
        "sample_min_index": minimum,
        "sample_max_index": maximum,
        "sample_index_count": count,
        "sample_span_bytes": (maximum - minimum + 1) * 4,
        "table_bytes": keys * 4,
    }


def check_anchor(current, previous):
    for field in ("expected_sha256", "stdout_hex", "input_size", "input_sha256",
                  "binary_sha256", "terminal_offset"):
        if current[field] != previous[field]:
            raise ValueError(f"archived Rust identity changed in {field}")
    for field in previous["oracle"]:
        if current["oracle"][field] != previous["oracle"][field]:
            raise ValueError(f"archived Rust endpoint replay disagrees in {field}")


def fixtures(args):
    previous = json.loads(FIRST_MANIFEST.read_bytes())
    old = {c["workload_id"]: c for c in previous["cases"]}
    template = old["rust_original"]
    program = {k: template[k] for k in ("binary_archive", "archive_member", "binary_sha256", "terminal_offset")}
    binary = SOURCE["read_program"](ROOT / program["binary_archive"], program["archive_member"])
    sha = SOURCE["sha"]
    if sha(binary) != program["binary_sha256"] or SOURCE["inspect"](binary, "rust") != program["terminal_offset"]:
        raise ValueError("unexpected Rust lower-bound ELF or comparison offset")

    inputs_dir = args.inputs or (args.materialize / "inputs" if args.materialize else None)
    cases = []
    for name, settings in CASES.items():
        anchor = old[settings] if isinstance(settings, str) else None
        if anchor:
            prior = anchor["oracle"]
            keys, queries, seed = (prior[key] for key in ("keys", "queries", "seed"))
            order = prior["order"]
        else:
            keys, queries, seed = settings
            order = "random"
        targets = SOURCE["query_sequence"](keys, queries, seed)
        oracle = SOURCE["input_oracle"](keys, targets, seed, order)
        oracle.update(sample_span(keys, targets))
        if anchor:
            oracle["sorted_targets_sha256"] = sha(SOURCE["little_endian_u32"](sorted(targets)))
        del targets
        stdin = f"{keys} {queries} {seed}\n".encode()
        stdout = f"{oracle['checksum']}\n".encode()
        row = {
            "workload_id": name, **program, "input_file": name + ".in", "input_size": len(stdin),
            "stdin_hex": stdin.hex(), "input_sha256": sha(stdin), "stdout_hex": stdout.hex(),
            "expected_sha256": sha(stdout), "oracle": oracle,
        }
        if anchor:
            check_anchor(row, anchor)
        if inputs_dir:
            SOURCE["write_verified"](inputs_dir / row["input_file"], stdin, 0o600)
        if args.materialize:
            elf = args.materialize / "elfs" / name / "Main"
            SOURCE["write_verified"](elf, binary, 0o700)
            if args.run:
                child = subprocess.run([elf], input=stdin, capture_output=True, check=True, timeout=60)
                if child.stdout != stdout or child.stderr:
                    raise ValueError("unexpected native output for " + name)
        cases.append(row)

    result = {"schema": 1, "first_fixtures_sha256": sha(FIRST_MANIFEST.read_bytes()), "cases": cases}
    if args.publish:
        with MANIFEST.open("x") as output:
            output.write(json.dumps(result, indent=2) + "\n")
    elif json.loads(MANIFEST.read_bytes()) != result:
        raise ValueError("registered inputs or expected results changed")
    print("Verified 12 inputs, one original Rust ELF, two repeated cases and all endpoint oracles.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publish", action="store_true", help="create the initial fixture manifest")
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--materialize", type=Path, help="write inputs and ELFs under this directory")
    output.add_argument("--inputs", type=Path, help="write the registered literal inputs under this directory")
    parser.add_argument("--run", action="store_true", help="check native stdout of each materialized program")
    args = parser.parse_args()
    if args.run and not args.materialize:
        parser.error("--run requires --materialize")
    fixtures(args)
