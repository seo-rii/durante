"""Reproduce the ten cases and generate ELF-bound site profiles using only #78 live-span receipts."""
import argparse
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path
import tarfile

HERE = Path(__file__).resolve().parent
CALIBRATION = HERE.parent / "results/gce-cpp20-locality"
ORIGINAL = HERE.parent / "cpp20_locality/cases.json"
ORIGINAL_SHA = "191e92e9baee86a53f3b05123d4149e3c624ed53bc7898d41b2d7c2bbd745d02"


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def require(ok, msg):
    if not ok:
        raise ValueError(msg)


def fixtures():
    raw = ORIGINAL.read_bytes()
    require(digest(raw) == ORIGINAL_SHA, "wrong #78 case manifest")
    original = json.loads(raw)
    cases = [{**c, "split": "repeat"} for c in original]
    program = {c["program"]: c["binary_sha256"] for c in original}
    # Independent direct traversal of every visit, recording only visits 0,1 mod 1024.
    entries, steps = 65536, 2097573
    at = 713
    sampled = []
    for i in range(steps):
        if i % 1024 < 2:
            sampled.append(at)
        at = (at * 1664525 + 1013904223) % entries
    near = [sum(abs((a + phase) // 16 - (b + phase) // 16) <= 1
                for a, b in zip(sampled[::2], sampled[1::2])) for phase in range(16)]
    oracle = {"offset": 6656, "visits": steps, "pairs": len(sampled) // 2,
              "sampled_min_index": min(sampled), "sampled_max_index": max(sampled),
              "sampled_span_bytes": 4 * (max(sampled) - min(sampled)), "near_pairs_by_base_mod64_div4": near}
    # sort/2 and rep_copy/2 are the exact published Aonohako #154 inputs and outputs.
    for name, stdin, stdout in (("cycle_walk/256KiB", f"{entries} {steps}\n", f"{at}\n"),
                                ("sort/2", "1048576\n", "384307717958270976\n"),
                                ("rep_copy/2", "4194304 1024\n", "1121503280051933\n")):
        binary = name.split("/")[0]
        case = {"case_id": name, "program": binary, "split": "holdout", "binary_sha256": program[binary],
                "stdin": stdin, "stdout": stdout, "stdin_sha256": digest(stdin.encode()),
                "stdout_sha256": digest(stdout.encode())}
        if binary == "cycle_walk":
            case.update(active_bytes=4 * entries, steps=steps, oracle=oracle)
        cases.append(case)
    return cases


def select(counts):
    total = sum(counts.values())
    require(total > 0, "empty profile calibration")
    prefix = []
    covered = 0
    for offset, visits in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))[:64]:
        prefix.append({"offset": offset, "visits": visits})
        covered += visits
        if 100 * covered >= 95 * total:
            break
    return {"offsets": sorted(s["offset"] for s in prefix), "ranked_prefix": prefix,
            "eligible_visits": total, "selected_visits": covered}


def profiles():
    cases = {c["case_id"]: c for c in json.loads(ORIGINAL.read_bytes())}
    dataset_path, receipt_path = CALIBRATION / "dataset.json.gz", CALIBRATION / "receipts.tar.xz"
    dataset = gzip.decompress(dataset_path.read_bytes())
    rows = json.loads(dataset)["rows"]
    counts = {c["binary_sha256"]: Counter() for c in cases.values()}
    seen = set()
    with tarfile.open(receipt_path, "r:xz") as archive:
        for row in rows:
            if row["arm"] != "live_span":
                continue
            name, rep = row["case_id"], row["rep"]
            require(name in cases and rep in range(1, 7) and (name, rep) not in seen, "invalid calibration row")
            member = f"runs/{row['sequence']:03d}/sample.json"
            raw = archive.extractfile(member).read()
            require(digest(raw) == row["receipts"][member], "calibration receipt mismatch")
            r = json.loads(raw)
            require(r["schema"] == 2 and r["pid"] == row["launcher_pid"] and
                    sum(s["loads"] for s in r["sites"]) == r["loads"], "wrong calibration ABI")
            seen.add((name, rep))
            counts[cases[name]["binary_sha256"]].update({s["offset"]: s["loads"] for s in r["sites"]})
    require(len(seen) == 6 * len(cases), "incomplete calibration")
    table = {}
    for elf, values in counts.items():
        inputs = [c for c in cases.values() if c["binary_sha256"] == elf]
        table[elf] = {"program": inputs[0]["program"], "training_cases": [c["case_id"] for c in inputs], **select(values)}
    return {"schema": 1, "arm": "live_span", "dataset_sha256": digest(dataset),
            "receipts_sha256": digest(receipt_path.read_bytes()), "by_elf": table}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="regenerate the two manifests")
    args = parser.parse_args()
    for name, obj in (("cases.json", fixtures()), ("profiles.json", profiles())):
        raw = (json.dumps(obj, indent=2) + "\n").encode()
        if args.write:
            (HERE / name).write_bytes(raw)
        else:
            require((HERE / name).read_bytes() == raw, f"different {name}")
        print(f"{name} {digest(raw)}")
