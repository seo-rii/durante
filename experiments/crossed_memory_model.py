#!/usr/bin/env python3
"""Freeze two six-feature CPP20 fits before releasing the crossed study's prospective cases."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
from statistics import median

from branch_load_interaction import DENOMINATOR, fit, identity, predict, require
from memory_cost_model import CANDIDATE, LABELS, LABELS_SHA256, samples, training_rows


ORIGINAL = Path(__file__).resolve().parent / "results/cpp20-v8-memory"
ORIGINAL_FEATURES_SHA256 = "46c988d6f38110f28911f7e5c7c260e075375671d5fccfc999c508ed09a2ed82"
STUDY_SHA256 = "2c9ca0d633c62193e6012b5baf4258668cacf5953da0785805a1bd44ae395716"
ORIGINAL_FIT_SHA256 = "e26b1001a473e22d72ac9dbad5fac463a9063e0e6a62eb488190ffcdd190efcd"
CALIBRATION = {f"cross-rep{rep}-{size}" for rep in (0, 256) for size in ("small", "medium", "large")}
EXTERNAL = {"sparse-rmq", "histogram-stack", "distinct-window"}


def read_pinned(path, expected):
    encoded = path.read_bytes()
    require(sha256(encoded).hexdigest() == expected, f"{path.name}: SHA-256 mismatch")
    return json.loads(encoded)


def native_target(case):
    rounds = case["rounds"]
    require([r["repetition"] for r in rounds] == [1, 2, 3], "need three ordered native/v8/native brackets")
    times = []
    for row in rounds:
        require(row["v8_sample"]["status"] == "Accepted" and row["v8_sample"]["termination"] == "stdout",
                "incomplete calibration v8 execution")
        for arm in ("native_before", "native_after"):
            sample = row[arm]
            ns = row[arm + "_ns"]
            require(sample["status"] == "Accepted" and sample["exit_code"] == 0 and
                    sample["cpu_source"] == "wait_rusage" and sample["verdict_source"] == "stdout" and
                    type(ns) is int and ns > 0 and ns == sample["cpu_ns"], "invalid native CPU observation")
            times.append(ns)
    return median(times)


def new_cases(features, calibration, manifest, historical):
    require(set(features["calibration"]) == set(calibration["cases"]) == set(manifest["calibration"]) == CALIBRATION,
            "wrong additional calibration cohort")
    require(set(features["external"]) == set(manifest["external"]) == EXTERNAL and
            set(manifest["cases"]) == CALIBRATION | EXTERNAL, "wrong prospective cohort")
    old_elfs = {case["elf_sha256"] for group in ("training", "external") for case in historical[group].values()}
    used_inputs = set()
    calibration_elfs = set()
    external_elfs = set()
    training, external = [], {}
    for group in ("calibration", "external"):
        for name, case in sorted(features[group].items()):
            ids = identity(case)
            fixture = manifest["cases"][name]
            expected = {"elf_sha256": fixture["elf_sha256"], "input_sha256": fixture["stdin_sha256"],
                        "output_sha256": fixture["stdout_sha256"]}
            require(ids == expected, f"{name}: frozen fixture mismatch")
            elf = ids["elf_sha256"]
            pair = (elf, ids["input_sha256"])
            require(pair not in used_inputs and elf not in old_elfs, f"{name}: reused workload")
            used_inputs.add(pair)
            rows = samples(case)
            if group == "calibration":
                label = calibration["cases"][name]
                require(ids == identity(label), f"{name}: calibration label identity mismatch")
                calibration_elfs.add(elf)
                training.append({"rows": rows, "native": native_target(label)})
            else:
                require(elf not in calibration_elfs | external_elfs, f"{name}: reused external executable")
                external_elfs.add(elf)
                external[name] = (ids, rows)
    require(len(calibration_elfs) == 1, "crossed calibration requires a common executable")
    return training, external


def freeze(features, calibration, manifest, historical, labels, prior):
    require(features["schema"] == calibration["schema"] == manifest["schema"] == 1, "unsupported schema")
    provenance = features["provenance"]
    require(provenance["protocol_sha256"] == calibration["experiment_protocol_sha256"] == STUDY_SHA256,
            "experiment protocol mismatch")
    require(calibration["stage"] == "calibration" and calibration["http_responses"] == 54,
            "expected six calibration cases with three triplets each")
    for key in ("local_sha256", "worker_commit", "probe_options", "production_options", "launcher_options", "cache_probe_options"):
        require(provenance[key] == historical["provenance"][key], f"changed diagnostic configuration: {key}")
    installation = calibration["v8_installation"]
    require(installation["unchanged_before_after"] is True and
            installation["source_commit"] == provenance["worker_commit"], "calibration worker identity mismatch")
    for local, hosted in (("meter", "client_sha256"), ("drrun", "launcher_sha256"), ("runtime", "release_core_sha256")):
        require(provenance["local_sha256"][local] == installation[hosted], "calibration runtime digest mismatch")
    original = training_rows(historical, labels)
    additional, external = new_cases(features, calibration, manifest, historical)
    models = {"control": fit(original, CANDIDATE), "expanded": fit(original + additional, CANDIDATE)}
    require(models["control"] == prior["models"]["candidate"], "original 15-case fit does not reproduce #64")
    cases = {}
    for name, (ids, rows) in external.items():
        estimates = {model: predict(rows, fitted) for model, fitted in models.items()}
        spreads = {}
        for field in CANDIDATE:
            counts = [r[field] for r in rows]
            middle = median(counts)
            spreads[field] = (max(counts) - min(counts)) / middle if middle else (None if max(counts) else 0)
        expanded = median(estimates["expanded"])
        cases[name] = {**ids, "predictions_ns": estimates, "control_median_ns": median(estimates["control"]),
                       "expanded_median_ns": expanded, "candidate_median_ns": expanded, "counter_spread": spreads}
    return {"schema": 1, "denominator": DENOMINATOR, "models": models, "external": cases}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("features", "calibration", "manifest"):
        parser.add_argument(name, type=Path)
    parser.add_argument("--calibration-sha256", required=True, help="independently sealed calibration SHA-256")
    parser.add_argument("--output", required=True, type=Path, help="new file to receive both frozen fits")
    args = parser.parse_args()
    try:
        historical = read_pinned(ORIGINAL / "features.json", ORIGINAL_FEATURES_SHA256)
        prior = read_pinned(ORIGINAL / "predictions.json", ORIGINAL_FIT_SHA256)
        labels = read_pinned(LABELS, LABELS_SHA256)
        calibration = read_pinned(args.calibration, args.calibration_sha256)
        encoded = args.features.read_bytes()
        features = json.loads(encoded)
        manifest_sha = calibration["manifest_sha256"]
        manifest = read_pinned(args.manifest, manifest_sha)
        require(features["provenance"]["manifest_sha256"] == manifest_sha, "feature manifest mismatch")
        result = freeze(features, calibration, manifest, historical, labels, prior)
    except (KeyError, TypeError, ValueError) as error:
        parser.error(str(error))
    result.update(features_sha256=sha256(encoded).hexdigest(), calibration_sha256=args.calibration_sha256,
                  manifest_sha256=manifest_sha, original_features_sha256=ORIGINAL_FEATURES_SHA256,
                  original_labels_sha256=LABELS_SHA256, original_fit_sha256=ORIGINAL_FIT_SHA256)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")


if __name__ == "__main__":
    main()
