"""Replay the registered across-family time estimates and retrospective deadline decisions."""
import argparse
from collections import Counter
from fractions import Fraction
import gzip
import hashlib
import itertools
import json
import math
from pathlib import Path
from statistics import median
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from load_model import determinant

MS = 1_000_000
RESULT = HERE.parent / "results/gce-guarded-native"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def views(record):
    """Return actual reports, keeping matched substitutes outside the delivered view."""
    result = {"full": [], "delivered": []}
    for row in record["rows"]:
        if row["kind"] in ("first", "scored") and row["full"] is not None:
            result["full"].append(row["full"])
        if row["delivery_source"] != "matched_full_substitute":
            result["delivered"].append(row["delivered"])
    require(all(result.values()), "missing full or delivered observations")
    return result


def native_samples(dataset, case_records):
    plan = (HERE / "protocol.json").read_bytes()
    require(dataset["protocol_sha256"] == hashlib.sha256(plan).hexdigest(), "wrong native protocol")
    slots = json.loads(plan)["schedule"]
    values = {n: {} for n in case_records}
    for i, (row, slot) in enumerate(zip(dataset["rows"], slots, strict=True)):
        n = row["case_id"]
        require(row["sequence"] == i and all(row[k] == v for k, v in slot.items()) and
                row["exit_code"] == 0 and row["stdout_sha256"] == case_records[n]["stdout_sha256"],
                "wrong native case or execution")
        require(all(type(row[k]) is int and row[k] >= 0 for k in ("user_us", "sys_us", "cpu_us"))
                and row["user_us"] + row["sys_us"] == row["cpu_us"] > 0, "invalid native wait4")
        if row["arm"] == "native":
            require(row["rep"] not in values[n], "duplicate native measurement")
            values[n][row["rep"]] = row["cpu_us"] * 1000
        else:
            require(row["arm"] == "warmup" and row["rep"] == 0, "unexpected native warmup")
    require(all(set(v) == set(range(1, 10)) for v in values.values()), "expected nine native observations per case")
    return {n: [v[i] for i in range(1, 10)] for n, v in values.items()}


def dot(a, b):
    return sum(x*y for x, y in zip(a, b, strict=True))


def fit(training, columns, counters, native, view):
    """Exact nonnegative relative least squares on all faces of a small Gram matrix."""
    centers = [[median([Fraction(r[k]) for r in counters[n][view]]) for k in columns] for n in training]
    design = [[v / median(native[n]) for v in center] for n, center in zip(training, centers, strict=True)]
    vectors = list(zip(*design, strict=True))
    gram = [[dot(a, b) for b in vectors] for a in vectors]
    rhs = [sum(col) for col in vectors]
    best = [Fraction(0)] * len(columns)
    best_loss, rank = len(training), 0
    for size in range(1, len(columns) + 1):
        for active in itertools.combinations(range(len(columns)), size):
            matrix = [[gram[i][j] for j in active] for i in active]
            denominator = determinant(matrix)
            if denominator == 0:
                continue
            rank = size
            weights = [Fraction(0)] * len(columns)
            for j, key in enumerate(active):
                numerator = [row[:j] + [rhs[i]] + row[j+1:] for i, row in zip(active, matrix, strict=True)]
                weights[key] = determinant(numerator) / denominator
            if any(weights[i] <= 0 for i in active):
                continue
            loss = sum((dot(row, weights)-1)**2 for row in design)
            if loss < best_loss:
                best_loss, best = loss, weights
    return {"weights_ns": dict(zip(columns, map(str, best), strict=True)), "rank": rank,
            "rank_deficient": rank < len(columns), "training_cases": list(training),
            "training_ranges": {k: [str(min(col)), str(max(col))]
                                for k, col in zip(columns, zip(*centers, strict=True), strict=True)}}


def predict(weights, observation):
    return math.ceil(sum(w * observation[k] for k, w in weights.items()))


def boundary(natives, predictions, limit):
    native_tle = sum(n > limit * MS for n in natives)
    model_tle = sum(p > limit * MS for p in predictions)
    if 0 < native_tle < len(natives):
        result = "mixed_native"
    elif native_tle == 0 and model_tle > 0:
        result = "false_tle"
    elif native_tle == len(natives) and model_tle < len(predictions):
        result = "false_accept"
    else:
        result = "agreement"
    return {"limit_ms": limit, "native_tle_reps": native_tle, "model_tle_reps": model_tle,
            "mixed_predictions": 0 < model_tle < len(predictions), "classification": result}


def score_case(natives, predictions, fixed_ms):
    target = median(natives)
    low = max(1, 4 * target // (5 * MS))
    high = math.ceil(Fraction(6 * target, 5 * MS))
    relative = [low, high] if low * MS < target < high * MS else []
    result = {"predictions_ns": predictions, "relative": [boundary(natives, predictions, b) for b in relative],
              "fixed": [boundary(natives, predictions, b) for b in fixed_ms]}
    for label, ref in (("median", target), ("minimum", min(natives))):
        errors = [Fraction(p, ref) - 1 for p in predictions]
        worst = max(errors, key=abs)
        result[label] = {"within_20pct": 5 * abs(worst) <= 1, "worst_signed_error_ppm": round(worst * MS)}
    return result


def summarize(results, names):
    records = [results[n] for n in names]
    out = {"cases": len(names)}
    for label in ("median", "minimum"):
        errors = [r[label]["worst_signed_error_ppm"] for r in records]
        out[label] = {"within_20pct": sum(r[label]["within_20pct"] for r in records),
                      "median_case_max_abs_error_ppm": round(median(list(map(abs, errors)))) if errors else None,
                      "worst_signed_error_ppm": max(errors, key=abs) if errors else None}
    for label in ("relative", "fixed"):
        items = [b for r in records for b in r[label]]
        tallies = Counter(b["classification"] for b in items)
        out[label] = {"boundaries": len(items), "mixed_predictions": sum(b["mixed_predictions"] for b in items),
                      **{k: tallies[k] for k in ("agreement", "mixed_native", "false_tle", "false_accept")}}
    out["excluded_relative_boundaries"] = 2 * len(records) - out["relative"]["boundaries"]
    return out


def predictions(names, fit_info, counters, native, view, fixed_ms):
    weights = {k: Fraction(v) for k, v in fit_info["weights_ns"].items()}
    return {n: score_case(native[n], [predict(weights, r) for r in counters[n][view]], fixed_ms) for n in names}


def evaluate(dataset_path):
    from guarded_native.evidence import evidence

    plan = json.loads((HERE / "design.json").read_bytes())
    source = evidence()
    cases = source["cases"]
    raw = dataset_path.read_bytes()
    raw = gzip.decompress(raw) if raw.startswith(b"\x1f\x8b") else raw
    native = native_samples(json.loads(raw), cases)
    counters = {n: views(c) for n, c in cases.items()}
    fixed_ms = plan["limits"]["fixed_ms"]
    programs = plan["case_groups"]
    groups = {label: [n for n, c in cases.items() if c["program"] in families] for label, families in programs.items()}
    require([len(groups[k]) for k in ("train", "heldout_real", "controls")] == [8, 5, 5]
            and len(set().union(*groups.values())) == len(cases) == 18, "different case split")
    real = groups["train"] + groups["heldout_real"]
    groups["quality_heldout_real"] = [n for n in groups["heldout_real"] if min(native[n]) >= 20 * MS
                                     and 10 * (max(native[n])-min(native[n])) <= median(native[n])]

    def fitted(training, names):
        result = {}
        for model, columns in plan["fit_models"].items():
            for view in ("delivered", "full"):
                params = fit(training, columns, counters, native, view)
                result[f"{view}/{model}"] = {**params, "cases": predictions(names, params, counters, native, view, fixed_ms)}
                if view == "delivered":
                    result[f"delivered_fit_full_input/{model}"] = {
                        **params, "cases": predictions(names, params, counters, native, "full", fixed_ms)}
        return result

    primary = fitted(groups["train"], cases)
    for model, key in (("instruction_rep", "combined_reference_weights"), ("instruction_movs_stos", "reference_weights")):
        params = {"weights_ns": source[key]}
        primary["frozen_154/" + model] = {**params, "cases": predictions(cases, params, counters, native, "delivered", fixed_ms)}
    for model in primary.values():
        model["summary"] = {key: summarize(model["cases"], names) for key, names in groups.items()}
        hold = model["summary"]["heldout_real"]
        rel = hold["relative"]
        model["primary_acceptance"] = (hold["median"]["within_20pct"] == hold["cases"] and
                                       rel["false_tle"] == rel["false_accept"] == 0)

    lofo = {}
    for program in programs["train"] + programs["heldout_real"]:
        held = [n for n in real if cases[n]["program"] == program]
        training = [n for n in real if cases[n]["program"] != program]
        for model, panel in fitted(training, held).items():
            item = lofo.setdefault(model, {"fits": {}, "cases": {}})
            item["cases"].update(panel.pop("cases"))
            item["fits"][program] = panel
    for model in lofo.values():
        model["summary"] = summarize(model["cases"], real)

    refs = {}
    for n, c in cases.items():
        old = c["archived_native_ns"]
        now = native[n]
        refs[n] = {"program": c["program"], "native_ns": now, "min_ns": min(now), "median_ns": median(now), "max_ns": max(now),
                   "native_spread_ppm": round(MS * Fraction(max(now) - min(now), median(now))),
                   "archived_median_ns": median(old) if old else None,
                   "native_median_change_ppm": round(MS * (Fraction(median(now), median(old)) - 1)) if old else None,
                   "reports": {k: len(v) for k, v in counters[n].items()}}
    return {"schema": 1, "dataset_sha256": hashlib.sha256(raw).hexdigest(),
            "design_sha256": hashlib.sha256((HERE / "design.json").read_bytes()).hexdigest(),
            "case_groups": groups, "native_reference": refs, "primary": primary, "leave_one_family_out": lofo}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path, nargs="?", default=RESULT / "dataset.json.gz")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--output", type=Path)
    group.add_argument("--verify", type=Path)
    args = parser.parse_args()
    value = evaluate(args.dataset)
    if args.verify:
        raw = args.verify.read_bytes()
        require(value == json.loads(gzip.decompress(raw) if raw.startswith(b"\x1f\x8b") else raw), "evaluation differs")
        print("Verified guarded native-time estimates and deadline decisions.")
    else:
        raw = (json.dumps(value, indent=2) + "\n").encode()
        if args.output:
            args.output.write_bytes(gzip.compress(raw, mtime=0) if args.output.suffix == ".gz" else raw)
        else:
            sys.stdout.buffer.write(raw)
