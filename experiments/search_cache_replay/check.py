"""Replay or verify the post-hoc lower-bound cache comparison on Linux with a C++17 compiler."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction
import gzip
import hashlib
import json
from pathlib import Path
from statistics import median
import subprocess
from tempfile import TemporaryDirectory

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DATA = ROOT / "experiments/results/gce-search-footprint"
FIXTURES = DATA / "fixtures.json"
DATASET = DATA / "dataset.json.gz"
ARTIFACT = DATA / "cache-alignment.json"
SOURCE = HERE / "replay.cpp"
CASES = ("prior_nonpower", "new_384k")
OFFSETS = range(0, 64, 4)


def require(value, message):
    if not value:
        raise ValueError(message)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def case_headers():
    fixtures = {c["workload_id"]: c for c in json.loads(FIXTURES.read_bytes())["cases"]}
    samples = json.loads(gzip.decompress(DATASET.read_bytes()))["samples"]
    result = {}
    for name in CASES:
        fixture = fixtures[name]
        oracle = fixture["oracle"]
        native = []
        rows = sorted((r for r in samples if r["workload_id"] == name), key=lambda r: r["repetition"])
        require([r["repetition"] for r in rows] == [1, 2, 3, 4], "missing registered rounds")
        for row in rows:
            require(row["binary_sha256"] == fixture["binary_sha256"] and
                    row["input_sha256"] == fixture["input_sha256"], "different search binary or input")
            for phase in ("native_before", "native_after"):
                child = row[phase]
                elapsed = child["user_ns"] + child["system_ns"]
                require(child["exit_code"] == 0 and elapsed == child["cpu_time_ns"] and elapsed > 0,
                        "invalid native timing")
                native.append(Fraction(elapsed))
        result[name] = {
            "keys": oracle["keys"], "queries": oracle["queries"], "seed": oracle["seed"],
            "input_sha256": fixture["input_sha256"], "expected_checksum": oracle["checksum"],
            "expected_loads": oracle["inner_loads"] + oracle["queries"],
            "native_median_ns": str(median(native)), "native_ns_per_query": str(median(native) / oracle["queries"]),
        }
    return result


def metadata():
    return {
        "schema": 1,
        "study": "Post-hoc simulation of key-array demand reads in two archived Rust lower-bound cases.",
        "fixtures_sha256": digest(FIXTURES),
        "compressed_native_dataset_sha256": digest(DATASET),
        "replay_cpp_sha256": digest(SOURCE),
        "policy": "Empty caches, least-recently-used replacement, 64-byte lines, four-byte elements. "
                  "Each modeled cache receives every inner and terminal comparison load in program order. "
                  "The traces exclude initialization, stack traffic, prefetch, page walks and other processes.",
        "models": {
            "l1_misses": {"bytes": 32768, "sets": 64, "ways": 8},
            "l2_misses": {"bytes": 524288, "sets": 1024, "ways": 8},
            "fully_associative_l1_misses": {"bytes": 32768, "sets": 1, "ways": 512, "offset_bytes": 0},
        },
    }


def simulate(executable, header, offset):
    command = [executable, *(str(header[k]) for k in ("keys", "queries", "seed")), str(offset)]
    raw = subprocess.check_output(command, text=True, timeout=180).split()
    fields = ["loads", "checksum", "l1_misses", "l2_misses"]
    if offset == 0:
        fields.append("fully_associative_l1_misses")
    return {"offset_bytes": offset, **dict(zip(fields, map(int, raw), strict=True))}


def check_rows(case, rows):
    require([r["offset_bytes"] for r in rows] == list(OFFSETS), "missing or unordered table alignment")
    for row in rows:
        fields = {"offset_bytes", "loads", "checksum", "l1_misses", "l2_misses"}
        if row["offset_bytes"] == 0:
            fields.add("fully_associative_l1_misses")
        require(set(row) == fields and all(type(x) is int for x in row.values()), "unexpected simulation counters")
        require(row["loads"] == case["expected_loads"] and row["checksum"] == case["expected_checksum"],
                "trace disagrees with the independent input oracle")
        require(all(0 < row[k] <= row["loads"] for k in fields if k.endswith("_misses")), "invalid miss count")


def run(quick, publish):
    headers = case_headers()
    result = metadata()
    if quick:
        saved = json.loads(ARTIFACT.read_bytes())
        require(set(saved) == set(result) | {"cases"} and all(saved[k] == v for k, v in result.items()),
                "cache study metadata differs")
        require(set(saved["cases"]) == set(headers), "unexpected case collection")
    result["cases"] = {}
    with TemporaryDirectory(prefix="durante-cache-") as tmp:
        executable = Path(tmp) / "replay"
        subprocess.run(["c++", "-std=c++17", "-O3", "-Wall", "-Wextra", "-Werror", SOURCE, "-o", executable],
                       check=True, timeout=60)
        subprocess.run([executable, "--self-test"], check=True, timeout=5)
        # All eight keys fit in one line at offset zero and two at byte 60.
        small = {"keys": 8, "queries": 1001, "seed": 123}
        for offset, expected in ((0, 1), (60, 2)):
            simple = simulate(executable, small, offset)
            require(simple["loads"] == 4004 and simple["l1_misses"] == simple["l2_misses"] == expected,
                    "small alignment control failed")
            if not offset:
                require(simple["fully_associative_l1_misses"] == expected, "fully associative control failed")
        for name, header in headers.items():
            if quick:
                stored = saved["cases"][name]
                require(set(stored) == set(header) | {"alignments"} and
                        all(stored[k] == v for k, v in header.items()), "different oracle or native median")
                rows = stored["alignments"]
            else:
                with ThreadPoolExecutor(max_workers=2) as pool:
                    rows = list(pool.map(lambda offset: simulate(executable, header, offset), OFFSETS))
            check_rows(header, rows)
            result["cases"][name] = {**header, "alignments": rows}
    if publish:
        with ARTIFACT.open("x") as dest:
            dest.write(json.dumps(result, indent=2) + "\n")
    elif result != json.loads(ARTIFACT.read_bytes()):
        raise ValueError("published post-hoc cache replay differs")
    print("Verified cache controls and published trace dimensions." if quick else
          "Reproduced both Rust inputs at all 16 cache-line alignments and both fully associative shadows.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--quick", action="store_true", help="check hand controls, source identities and archived counts")
    mode.add_argument("--publish", action="store_true", help="create the initial full post-hoc result")
    args = parser.parse_args()
    run(args.quick, args.publish)
