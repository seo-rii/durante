#!/usr/bin/env python3
"""Fit and score timing hypotheses from CPP20 branch-probe replays and native CPU brackets."""
import argparse
import hashlib
import json
from math import isfinite, isqrt
from pathlib import Path
import re
from statistics import median


COUNTERS = ("instructions", "rep_movs_bytes", "rep_stos_bytes", "latency_bound_x16",
            "load_instructions", "gshare_mispredicts")
INTERACTION = "load_branch_interaction"
MODELS = {"cheap": COUNTERS[:3], "linear_control": COUNTERS,
          "interaction": (*COUNTERS[:-1], INTERACTION)}
DIGESTS = ("elf_sha256", "input_sha256", "output_sha256")
DENOMINATOR = 1_000_000_000


def require(condition, message):
    if not condition:
        raise ValueError(message)


def identity(case):
    result = {key: case[key] for key in DIGESTS}
    require(all(isinstance(v, str) and re.fullmatch("[a-f0-9]{64}", v) for v in result.values()),
            "invalid case identity")
    return result


def read_samples(case):
    identity(case)
    rows = case["counters"]
    require(len(rows) == 3, "expected three diagnostic replays")
    result = []
    for row in rows:
        require(set(row) == set(COUNTERS) and all(type(v) is int and v >= 0 for v in row.values()),
                "invalid diagnostic counters")
        instructions, loads, misses = row["instructions"], row["load_instructions"], row["gshare_mispredicts"]
        require(instructions > 0 and loads <= instructions and misses <= instructions, "inconsistent counts")
        result.append({**row, INTERACTION: isqrt(loads * misses)})
    return result


def native_median(case):
    samples = case["native_ns"]
    require(len(samples) == 6 and all(type(v) is int and v > 0 for v in samples),
            "expected six positive native CPU readings")
    return median(samples)


def fit(training, fields):
    import numpy as np
    from scipy.optimize import nnls

    # Normalize each column using training rows only. A missing operation in a
    # leave-one-family-out fold cannot acquire a coefficient in that fold.
    x = np.array([[median(row[key] for row in case["rows"]) for key in fields]
                  for case in training], dtype=float)
    y = np.array([case["native"] for case in training], dtype=float)
    scale, target_scale = x.max(axis=0), np.median(y)
    active = scale > 0
    design = (x[:, active] / scale[active]) / (y / target_scale)[:, None]
    coefficients = np.zeros(len(fields))
    coefficients[active] = nnls(design, np.ones(len(y)))[0] * target_scale / scale[active]
    return {"weights": {key: round(float(v) * DENOMINATOR) for key, v in zip(fields, coefficients)},
            "unobserved_training_features": [key for key, available in zip(fields, active) if not available]}


def predict(rows, fitted):
    weights = fitted["weights"]
    return [(sum(row[key] * weight for key, weight in weights.items()) + DENOMINATOR - 1) // DENOMINATOR
            for row in rows]


def errors(predictions, target):
    return [value / target - 1 for value in predictions]


def summary(values):
    require(bool(values), "empty prediction group")
    return {"predictions": len(values), "within_20_percent": sum(abs(x) <= 0.2 + 1e-15 for x in values),
            "max_abs_error_percent": 100 * max(map(abs, values))}


def freeze(data):
    require(data["schema"] == 1, "unsupported study schema")
    calibration, external = data["training"], data["external"]
    require(bool(calibration) and bool(external) and not (calibration.keys() & external.keys()),
            "expected distinct training and external case IDs")
    for cohort in (calibration, external):
        identities = [(case["elf_sha256"], case["input_sha256"]) for case in cohort.values()]
        require(len(set(identities)) == len(identities), "duplicate executable/input pair in cohort")
    training = []
    for name, case in sorted(calibration.items()):
        require(isinstance(case["family"], str) and case["family"], "missing training family")
        training.append({"id": name, "family": case["family"], "rows": read_samples(case),
                         "native": native_median(case)})
    families = sorted({r["family"] for r in training})
    require(len(families) >= 2, "need at least two training families")
    known_elfs = {case["elf_sha256"] for case in calibration.values()}
    cases, query_rows = {}, {}
    for name, case in sorted(external.items()):
        require(set(case) == {*DIGESTS, "counters"}, "external predictors must contain only identity and counters")
        require(case["elf_sha256"] not in known_elfs, "external ELF was used for fitting")
        rows = read_samples(case)
        query_rows[name] = rows
        centers = {k: median(row[k] for row in rows) for k in COUNTERS}
        spread = {}
        for k in COUNTERS:
            counts = [r[k] for r in rows]
            # A zero median and a nonzero maximum has unbounded relative spread.
            spread[k] = ((max(counts) - min(counts)) / centers[k] if centers[k]
                         else None if max(counts) else 0.0)
        cases[name] = {**identity(case), "counter_spread": spread, "estimates_ns": {}}
    models = {}
    for label, fields in MODELS.items():
        folds = {}
        for family in families:
            fitted = fit([r for r in training if r["family"] != family], fields)
            residuals = [e for r in training if r["family"] == family
                         for e in errors(predict(r["rows"], fitted), r["native"])]
            folds[family] = {**summary(residuals), "unobserved_training_features": fitted["unobserved_training_features"]}
        fitted = fit(training, fields)
        models[label] = {**fitted, "family_exclusion": folds}
        for name, rows in query_rows.items():
            cases[name]["estimates_ns"][label] = predict(rows, fitted)
    return {"schema": 1, "weight_denominator": DENOMINATOR, "models": models, "external": cases}


def score(frozen, labels):
    require(frozen["schema"] == labels["schema"] == 1, "unsupported results schema")
    require(bool(frozen["external"]) and frozen["external"].keys() == labels["cases"].keys(),
            "held-out label set does not match frozen predictions")
    result = {}
    groups = {k: [] for k in MODELS}
    for name, case in sorted(labels["cases"].items()):
        frozen_case = frozen["external"][name]
        require(identity(case) == identity(frozen_case), f"{name}: mismatched artifact, input or output")
        target = native_median(case)
        spread = (max(case["native_ns"]) - min(case["native_ns"])) / target
        spreads = frozen_case["counter_spread"]
        require(set(spreads) == set(COUNTERS) and all(
            s is None or (type(s) in (int, float) and isfinite(s) and s >= 0) for s in spreads.values()),
            "invalid frozen counter spreads")
        rows = {}
        for label in MODELS:
            values = frozen_case["estimates_ns"][label]
            require(len(values) == 3 and all(type(v) is int and v >= 0 for v in values), "invalid estimates")
            relative = errors(values, target)
            groups[label].extend(relative)
            rows[label] = {"estimates_ns": values, "signed_error_percent": [100 * e for e in relative]}
        result[name] = {"native_median_ns": target, "native_spread": spread,
                        "native_duration_pass": min(case["native_ns"]) >= 20_000_000,
                        "native_spread_pass": spread <= 0.1,
                        "counter_spread_pass": all(s is not None and s <= 0.001
                                                   for s in spreads.values()),
                        "models": rows}
    return {"schema": 1, "cases": result, "accuracy": {k: summary(v) for k, v in groups.items()}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    f = commands.add_parser("freeze", help="fit training controls and predict using unlabeled external counters")
    f.add_argument("--input", type=Path, required=True)
    s = commands.add_parser("score", help="join frozen predictions to separately collected native readings")
    s.add_argument("--frozen", type=Path, required=True)
    s.add_argument("--labels", type=Path, required=True)
    for cmd in (f, s):
        cmd.add_argument("--output", type=Path, required=True, help="new JSON file")
    args = parser.parse_args()
    try:
        if args.action == "freeze":
            raw = args.input.read_bytes()
            result = {**freeze(json.loads(raw)), "input_sha256": hashlib.sha256(raw).hexdigest()}
        else:
            raw = args.frozen.read_bytes()
            result = {**score(json.loads(raw), json.loads(args.labels.read_bytes())),
                      "frozen_sha256": hashlib.sha256(raw).hexdigest()}
        with args.output.open("x") as output:
            json.dump(result, output, indent=2, sort_keys=True, allow_nan=False)
            output.write("\n")
    except (KeyError, ValueError, TypeError, OSError, RuntimeError) as exc:
        parser.exit(1, f"branch/load experiment failed: {exc}\n")


if __name__ == "__main__":
    main()
