#!/usr/bin/env python3
"""Evaluate two predeclared offline models on held-out CPP20 workload families."""
import argparse
from fractions import Fraction
import hashlib
from itertools import combinations
import json
from pathlib import Path
from statistics import median


FAMILIES = {
    "training": ("integer", "rep_copy", "sort"),
    "historical": ("branch", "rep_fill"),
    "unseen": ("lower_bound", "window_sum"),
}
SCALES = (1, 2, 4)
REPEATS = 3
FEATURES = ("instructions", "rep_bytes", "load_instructions")
MODELS = {"baseline": FEATURES[:2], "with_loads": FEATURES}
MIN_NORMALIZED_DETERMINANT = Fraction(1, 10**12)
MAX_RELATIVE_ERROR = Fraction(1, 5)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def integer(value, low=0):
    return type(value) is int and low <= value < 2**64


def distribution(values):
    values = sorted(values)
    # Match the reference collector's round-up convention for an even median.
    median = (values[(len(values) - 1) // 2] + values[len(values) // 2] + 1) // 2
    span = values[-1] - values[0]
    spread = span / median if median else (0.0 if span == 0 else None)
    return {"min": values[0], "median": median, "max": values[-1], "relative_spread": spread}


def read_cases(data, families=FAMILIES, counters=FEATURES):
    require(type(data["schema"]) is int and data["schema"] == 1 and data["complete"] is True and
            "failure" not in data and data["cpuid_profile"] == "x86-64-v2",
            "expected a complete fixed-CPUID x86-64-v2 collection")
    require(type(data["repetitions"]) is int and data["repetitions"] == REPEATS,
            "expected three repetitions")
    roles = {family: role for role, names in families.items() for family in names}
    summaries = {}
    samples = {}
    for case in data["cases"]:
        family, scale = case["family"], case["scale"]
        require(family in roles and integer(scale, 1) and scale in SCALES, "unexpected family or scale")
        key = f"{family}-{scale}"
        require(key not in samples, f"duplicate case {key}")
        rows = case["repetitions"]
        require(all(integer(row["repetition"], 1) for row in rows) and
                sorted(row["repetition"] for row in rows) == list(range(1, REPEATS + 1)),
                f"{key}: missing or duplicate repetition")
        native = []
        for row in rows:
            require(all(integer(row[k], 1) for k in ("native_before_ns", "native_after_ns", "instructions"))
                    and all(integer(row[k]) for k in counters)
                    and row["load_instructions"] <= row["instructions"], f"{key}: invalid timing or counter")
            native.extend((row["native_before_ns"], row["native_after_ns"]))
        summaries[key] = {
            "family": family, "scale": scale, "group": roles[family], "native_ns": distribution(native),
            "counters": {k: distribution(row[k] for row in rows) for k in counters},
        }
        samples[key] = sorted(rows, key=lambda row: row["repetition"])
    require(len(samples) == len(roles) * len(SCALES), "incomplete family/scale coverage")
    return summaries, samples


def determinant(matrix):
    """Exact cofactor expansion for the small predeclared systems."""
    if len(matrix) == 1:
        return matrix[0][0]
    return sum((-1)**j * value * determinant([row[:j] + row[j+1:] for row in matrix[1:]])
               for j, value in enumerate(matrix[0]))


def fit(training, names):
    # Equal weight per family/scale. Each row is one median feature vector
    # divided by its bracketed native median; the regression target is one.
    design = [[Fraction(case["counters"][k]["median"], case["native_ns"]["median"]) for k in names]
              for case in training]
    cols = list(zip(*design))
    gram = [[sum(x*y for x, y in zip(a, b)) for b in cols] for a in cols]
    diag_product = 1
    for i in range(len(names)):
        diag_product *= gram[i][i]
    normalized_det = determinant(gram) / diag_product if diag_product else Fraction(0)
    if normalized_det < MIN_NORMALIZED_DETERMINANT:
        return normalized_det, None

    # The nonnegative optimum lies on one of the 2**len(names) faces. Solve each
    # restricted least-squares system exactly by Cramer's rule and retain its
    # solution only if every active coefficient is nonnegative.
    best_weights = [Fraction(0)] * len(names)
    best_loss = len(design)
    rhs = [sum(col) for col in cols]
    for size in range(1, len(names) + 1):
        for active in combinations(range(len(names)), size):
            matrix = [[gram[i][j] for j in active] for i in active]
            det = determinant(matrix)  # Full rank guarantees positive principal minors.
            weights = [Fraction(0)] * len(names)
            for pos, j in enumerate(active):
                numer = [row[:pos] + [rhs[i]] + row[pos+1:] for row, i in zip(matrix, active)]
                weights[j] = determinant(numer) / det
            if min(weights) < 0:
                continue
            loss = sum((sum(x*w for x, w in zip(row, weights)) - 1)**2 for row in design)
            if loss < best_loss:
                best_loss, best_weights = loss, weights
    return normalized_det, best_weights


def evaluate(data, *, families=FAMILIES, counters=FEATURES, models=MODELS):
    cases, samples = read_cases(data, families, counters)
    training = [case for case in cases.values() if case["group"] == "training"]
    result = {"schema": 1, "cpuid_profile": "x86-64-v2", "families": families,
              "max_relative_error": float(MAX_RELATIVE_ERROR),
              "min_normalized_gram_determinant": float(MIN_NORMALIZED_DETERMINANT),
              "cases": cases, "models": {}}
    for label, names in models.items():
        normalized_det, weights = fit(training, names)
        model = {"features": names, "normalized_gram_determinant": float(normalized_det),
                 "identified": weights is not None, "accuracy_gate_pass": False}
        result["models"][label] = model
        if weights is None:
            model["reason"] = "training feature matrix is rank deficient or ill-conditioned"
            continue
        model["diagnostic_ns_per_count"] = {k: float(w) for k, w in zip(names, weights)}
        model["cases"] = {}
        model["groups"] = {}
        for group in ("historical", "unseen"):
            group_rows = []
            for key, case in cases.items():
                if case["group"] != group:
                    continue
                median = case["native_ns"]["median"]
                rows = []
                for row in samples[key]:
                    estimate = sum(row[k] * w for k, w in zip(names, weights))
                    error = (estimate - median) / median
                    rows.append({"repetition": row["repetition"], "estimate_ns": float(estimate),
                                 "relative_error": float(error), "within_20_percent": abs(error) <= MAX_RELATIVE_ERROR})
                errors = [r["relative_error"] for r in rows]
                model["cases"][key] = {"predictions": rows, "signed_error_range": [min(errors), max(errors)]}
                group_rows.extend(rows)
            model["groups"][group] = score(group_rows)
        model["groups"]["all"] = score([r for case in model["cases"].values() for r in case["predictions"]])
        model["accuracy_gate_pass"] = model["groups"]["unseen"]["pass"]
    return result


def score(rows):
    passed = sum(row["within_20_percent"] for row in rows)
    return {"passed": passed, "total": len(rows), "pass": passed == len(rows),
            "max_absolute_relative_error": max(abs(row["relative_error"]) for row in rows)}


def check_corpus(data, corpus_bytes, *, expected=None):
    if expected is None:
        expected = json.loads(corpus_bytes)
    require(data["corpus_sha256"] == hashlib.sha256(corpus_bytes).hexdigest() and
            data["compiler_image"] == expected["compiler_image"], "unexpected corpus or compiler image")
    for case in data["cases"]:
        fixture = expected["families"][case["family"]]
        inputs = {c["scale"]: c["stdin"] for c in fixture["cases"]}
        expected_stdin = inputs[case["scale"]].encode()
        require(case["input_sha256"] == hashlib.sha256(expected_stdin).hexdigest() and
                case["binary_sha256"] == fixture["binary_sha256"],
                f"{case['family']}/{case['scale']}: binary or input does not match the corpus")


def overhead(cases):
    """Descriptive only. Give each matched probe/meter execution pair equal weight."""
    def summary(ratios):
        return {"pairs": len(ratios), "min": float(min(ratios)), "median": float(median(ratios)),
                "max": float(max(ratios))}

    per_case = {}
    all_ratios = []
    for case in cases:
        ratios = []
        for row in case["repetitions"]:
            require(integer(row["meter_cpu_ns"], 1) and integer(row["probe_cpu_ns"], 1),
                    "expected positive paired meter and probe CPU times")
            ratios.append(Fraction(row["probe_cpu_ns"], row["meter_cpu_ns"]))
        per_case[f"{case['family']}-{case['scale']}"] = summary(ratios)
        all_ratios.extend(ratios)
    return {"all": summary(all_ratios), "cases": per_case}


def run_cli(evaluator, corpus_path, description, *, check_manifest=check_corpus):
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="new diagnostic JSON file")
    args = parser.parse_args()
    try:
        raw = args.input.read_bytes()
        data = json.loads(raw)
        check_manifest(data, corpus_path.read_bytes())
        result = evaluator(data)
        result["descriptive_probe_to_meter_cpu_ratio"] = overhead(data["cases"])
        result["corpus_sha256"] = data["corpus_sha256"]
        result["compiler_image"] = data["compiler_image"]
        result["input_sha256"] = hashlib.sha256(raw).hexdigest()
        payload = json.dumps(result, indent=2, allow_nan=False) + "\n"
        with args.output.open("x") as output:
            output.write(payload)
    except (KeyError, TypeError, ValueError, OSError) as exc:
        parser.exit(1, f"model evaluation failed: {exc}\n")
    print(args.output)


def main():
    run_cli(evaluate, Path(__file__).with_name("load_corpus.json"), __doc__)


if __name__ == "__main__":
    main()
