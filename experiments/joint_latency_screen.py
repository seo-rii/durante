#!/usr/bin/env python3
"""Test a five-counter timing inequality against the archived CPP20 gshare runs."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import tarfile

from collect_branch_model import BRANCH_CORPUS, branch_counts
from collect import aggregate, parse_records
from gshare_model import FAMILIES, MODELS, check_manifest
from load_model import integer, read_cases, require


RESULTS = Path(__file__).resolve().with_name("results")
REFERENCE = RESULTS / "cpp20-gshare-model.json"
ARCHIVE = RESULTS / "cpp20-gshare-probe-records.tar.gz"
REFERENCE_SHA256 = "ea412fb0779d6f5af1f83580e5ec9e1c117d21174b0babb9ec7b811ac3740c4e"
ARCHIVES = {
    "75a9845f72a122d79af3a88f81c00f8f18359a26cd5e12524296d7d11b0ab4e2",  # public subset
    "74ea30e878b6ea6769adce061a7090f4f4838b0a8421872e2ddacfcdb3b19709",  # original collection
}
FEATURES = (*MODELS["with_gshare"], "latency_bound_x16")


def recover_bound(row, features, pid, raw):
    meter = aggregate({pid: parse_records(pid, raw)}, pid)
    require(meter["complete"] and meter["valid"] and meter["quality"] == "uninterrupted"
            and meter["cpuid_profile"] == "x86-64-v2" and meter["rep_bytes_enabled"]
            and meter["process_count"] == meter["image_count"] == 1 and meter["segments"][0]["threads"] == 1
            and meter["processes"][0]["exit_code"] == 0 and meter["unsupported_rep_entries"] == 0
            and meter["latency_bound"]["version"] == 1, "incomplete or incompatible production receipt")
    counts = branch_counts(features, meter)
    require(all(row[k] == counts[k] for k in counts) and
            all(row[k] == meter[k] for k in ("instructions", "rep_bytes")),
            "probe and production counters do not match the published repetition")
    bound = meter["latency_bound"]["cycles_x16"]
    require(integer(features["bound_cycles_x16"]) and features["bound_cycles_x16"] == bound,
            "production and exploratory latency bounds disagree")
    return bound


def recover(archive_path):
    # Both archives have the same 81 dual-client executions. Read the expected
    # members in memory; publication drops filesystem ownership and timestamps.
    with archive_path.open("rb") as source:
        raw_archive = source.read(65537)
    require(len(raw_archive) <= 65536 and hashlib.sha256(raw_archive).hexdigest() in ARCHIVES,
            "unexpected CPP20 record archive")
    reference = REFERENCE.read_bytes()
    require(hashlib.sha256(reference).hexdigest() == REFERENCE_SHA256, "unexpected reference collection")
    data = json.loads(reference)
    check_manifest(data, BRANCH_CORPUS.read_bytes())

    with tarfile.open(fileobj=io.BytesIO(raw_archive), mode="r:gz") as archive:
        require(archive.extractfile("results/collection.json").read() == reference,
                "archive does not match the published collection")
        streams = {}
        for member in archive.getmembers():
            match = re.fullmatch(r"(results/[a-z_]+-[124]-[123]-probe)/records/process-([1-9][0-9]*)\.jsonl",
                                 member.name)
            if match:
                label, pid = match.groups()
                require(member.isfile() and label not in streams, "expected one process per probe run")
                streams[label] = int(pid), member
        for case in data["cases"]:
            for row in case["repetitions"]:
                prefix = f"results/{case['family']}-{case['scale']}-{row['repetition']}-probe"
                pid, member = streams.pop(prefix)
                features = json.load(archive.extractfile(prefix + "/features.json"))
                row["latency_bound_x16"] = recover_bound(row, features, pid, archive.extractfile(member).read())
        require(not streams, "unexpected dual-client process record")
    return data


def screen(data):
    cases, _ = read_cases(data, FAMILIES, FEATURES)
    search, sort, merge = (cases[k] for k in ("lower_bound-4", "sort-4", "merge_streams-2"))
    # Under nonnegative weights, componentwise domination implies domination
    # of the prediction. The coefficients on both sides sum to five.
    slacks = {k: 2 * sort["counters"][k]["min"] + 3 * merge["counters"][k]["min"]
              - 5 * search["counters"][k]["max"] for k in FEATURES}
    # Multiply both sides by 25 to retain exact ns for the ±20% tolerance
    # and the convex mixture (2/5 sort + 3/5 merge).
    lower = 20 * search["native_ns"]["min"]
    upper = 6 * (2 * sort["native_ns"]["max"] + 3 * merge["native_ns"]["max"])
    return {
        "collection_sha256": REFERENCE_SHA256,
        "latency_bound_x16": {
            f"{c['family']}-{c['scale']}": [r["latency_bound_x16"] for r in sorted(
                c["repetitions"], key=lambda r: r["repetition"])]
            for c in data["cases"]
        },
        "feature_slack_2sort_plus_3merge_minus_5search": slacks,
        "minimum_search_time_ns_x25": lower, "maximum_mixture_time_ns_x25": upper,
        "joint_accuracy_impossible": all(s >= 0 for s in slacks.values()) and lower > upper,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=ARCHIVE, help="public subset or original raw archive")
    args = parser.parse_args()
    try:
        result = screen(recover(args.archive))
    except (ValueError, KeyError, OSError, tarfile.TarError) as error:
        parser.exit(1, f"joint screen failed: {error}\n")
    print(json.dumps(result, indent=2))
