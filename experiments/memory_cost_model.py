#!/usr/bin/env python3
"""Freeze relative-error NNLS predictions for the CPP20 L1 data-cache experiment."""
import argparse
import hashlib
import json
from pathlib import Path
from statistics import median

from branch_load_interaction import DENOMINATOR, DIGESTS, fit, identity, native_median, predict, require


BASELINE = ("instructions", "rep_movs_bytes", "rep_stos_bytes", "loop_recurrence_cycles_x16",
            "gshare_mispredicts")
CANDIDATE = (*BASELINE, "l1d_misses")
FIELDS = (*CANDIDATE, "l1d_refs", "l1d_lines", "l1d_skipped", "l1d_rep")
MODELS = {"baseline": BASELINE, "candidate": CANDIDATE}
TRAINING = {f"{family}-{n}" for family in ("integer", "branch", "sort", "rep_copy", "rep_fill")
            for n in (1, 2, 4)}
EXTERNAL = {"fenwick-prefix", "kmp-match", "union-find"}
EXPERIMENTS = Path(__file__).resolve().parent
LABELS = EXPERIMENTS / "results/cpp20-v8-training.json"
MANIFEST = EXPERIMENTS / "memory_cost_holdout/manifest.json"
MANIFEST_SHA256 = "4b770da0e128ae3840aebc3c5654c020453b367f2bc4e8c812527a3787219338"
LABELS_SHA256 = "80f5bce9bfde037e0836cb00b5d71d1c13f1fe0eddc8995f9be27bd3ed2e90b4"


def samples(case):
    require(set(case) == {*DIGESTS, "counters"}, "expected only case identity and counters")
    rows = case["counters"]
    require(isinstance(rows, list) and len(rows) == 3, "expected three feature observations")
    for row in rows:
        require(isinstance(row, dict) and set(row) == set(FIELDS) and
                all(type(v) is int and v >= 0 for v in row.values()), "invalid feature vector")
        require(0 < row["instructions"] and row["gshare_mispredicts"] <= row["instructions"] and
                row["l1d_rep"] <= row["instructions"] and row["l1d_refs"] <= row["l1d_lines"] and
                row["l1d_misses"] <= row["l1d_lines"], "inconsistent instruction or cache counts")
    return rows


def training_rows(features, labels):
    require(features["schema"] == labels["schema"] == 1, "unknown input schema")
    require(set(features["training"]) == set(labels["training"]) == TRAINING, "wrong training cohort")
    seen = set()
    training = []
    for name, case in sorted(features["training"].items()):
        ids = identity(case)
        target = labels["training"][name]
        require(ids == identity(target), f"{name}: labels and measured artifacts differ")
        pair = (ids["elf_sha256"], ids["input_sha256"])
        require(pair not in seen, f"{name}: duplicate executable and input")
        seen.add(pair)
        training.append({"id": name, "rows": samples(case), "native": native_median(target)})
    return training


def freeze(features, labels):
    training = training_rows(features, labels)
    require(set(features["external"]) == EXTERNAL, "wrong prospective cohort")
    seen_elfs = {case["elf_sha256"] for case in features["training"].values()}
    models = {model: fit(training, fields) for model, fields in MODELS.items()}
    external = {}
    for name, case in sorted(features["external"].items()):
        ids = identity(case)
        require(ids["elf_sha256"] not in seen_elfs, f"{name}: reused prospective executable")
        seen_elfs.add(ids["elf_sha256"])
        rows = samples(case)
        estimates = {model: predict(rows, fitted) for model, fitted in models.items()}
        spread = {}
        for field in CANDIDATE:
            counts = [row[field] for row in rows]
            middle = median(counts)
            spread[field] = (max(counts) - min(counts)) / middle if middle else (None if max(counts) else 0)
        external[name] = {**ids, "predictions_ns": estimates, "counter_spread": spread,
                          "candidate_median_ns": median(estimates["candidate"]),
                          "baseline_median_ns": median(estimates["baseline"])}
    return {"schema": 1, "denominator": DENOMINATOR, "models": models, "external": external}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("features", type=Path, help="three observations per training and prospective case")
    parser.add_argument("--labels", type=Path, default=LABELS, help="published #62 training input")
    parser.add_argument("--output", type=Path, required=True, help="new file for the sealed predictions")
    args = parser.parse_args()
    feature_bytes, label_bytes = args.features.read_bytes(), args.labels.read_bytes()
    try:
        require(hashlib.sha256(label_bytes).hexdigest() == LABELS_SHA256, "unexpected training label file")
        manifest_bytes = MANIFEST.read_bytes()
        require(hashlib.sha256(manifest_bytes).hexdigest() == MANIFEST_SHA256, "unexpected prospective manifest")
        features = json.loads(feature_bytes)
        for name, description in json.loads(manifest_bytes)["cases"].items():
            expected = {"elf_sha256": description["elf_sha256"], "input_sha256": description["stdin_sha256"],
                        "output_sha256": description["stdout_sha256"]}
            require(identity(features["external"][name]) == expected, f"{name}: prospective manifest mismatch")
        result = freeze(features, json.loads(label_bytes))
    except (KeyError, TypeError, ValueError) as error:
        parser.error(str(error))
    result["features_sha256"] = hashlib.sha256(feature_bytes).hexdigest()
    result["labels_sha256"] = LABELS_SHA256
    result["manifest_sha256"] = MANIFEST_SHA256
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")


if __name__ == "__main__":
    main()
