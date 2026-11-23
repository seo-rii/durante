"""Compare frozen clocks with clocks calibrated on another submission for the same input."""
from collections import defaultdict
from fractions import Fraction
from math import ceil
from pathlib import Path
import re
from statistics import median
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from cpp20_locality.verify import require
from guarded_native import anchor
from guarded_native.score import MS
from guarded_native.transfer import scaled_ns, work

# Aonohako b938afaa6c171a8a56e24d376d6662f93fcea4c6, docs/durante/gce-v11-cpp20-accuracy/
# evaluation.json.gz, models.cpp20_train_only.latency_memory.weights_ns. The published
# coefficient is in ns per modeled cycle. The v11 counter reports sixteenths of a cycle.
LATENCY_DENOMINATOR = 25101905128725813306394866699319144848698923740306171229691994626199203
LATENCY_WEIGHTS = {
    "cycles_x16": Fraction(7794176051596458687892114206183043707591360178323787288974187281984000,
                           16 * LATENCY_DENOMINATOR),
    "memory_operand_bytes": Fraction(39348045374006158597826450670474644171765713848006688256332520795364000,
                                     LATENCY_DENOMINATOR),
}
METHODS = ("frozen_ns", "transfer_ns", "latency_frozen_ns", "latency_transfer_ns")


def positive(value):
    return type(value) is int and value > 0


def samples(values, count):
    require(isinstance(values, list) and len(values) == count and all(positive(v) for v in values),
            f"expected {count} positive native CPU readings in nanoseconds")


def predict(reference_counter, submission_counter, reference_samples):
    """Seal all four estimates using only the reference ELF's calibration times."""
    samples(reference_samples, 3)
    for counters in (reference_counter, submission_counter):
        for key in ("instructions", "rep_bytes", "cycles_x16", "memory_operand_bytes"):
            value = counters.get(key)
            require(type(value) is int and 0 <= value < 2**64, f"invalid {key} counter")
        require(counters["instructions"] > 0 and counters["cycles_x16"] > 0, "expected positive measured work")
    weights = {k: Fraction(v) for k, v in anchor.weights().items()}
    reference_ns = median(reference_samples)
    predictions = {}
    for frozen, transfer, coefficients in (("frozen_ns", "transfer_ns", weights),
                                           ("latency_frozen_ns", "latency_transfer_ns", LATENCY_WEIGHTS)):
        current = work(submission_counter, coefficients)
        predictions[frozen] = ceil(current)
        predictions[transfer] = scaled_ns(reference_ns, current, work(reference_counter, coefficients))
    return predictions


def native_range(values):
    lo, center, hi = min(values), median(values), max(values)
    return dict(samples=len(values), min_ns=lo, median_ns=center, max_ns=hi,
                spread_ppm=round(MS * Fraction(hi - lo, center)))


def deadlines(prediction, native, limits):
    points = []
    for limit in limits:
        over = sum(value > limit * MS for value in native)
        predicted_tle = prediction > limit * MS
        majority_tle = 2 * over > len(native)
        points.append(dict(limit_ms=limit, native_tle=over, model_tle=predicted_tle,
                           mixed=0 < over < len(native), majority_correct=predicted_tle == majority_tle))
    return points


def score_case(row):
    estimates = predict(row["reference_counter"], row["submission_counter"], row["reference_samples_ns"])
    sealed = row["predictions"]
    require(isinstance(sealed, dict) and set(sealed) == set(METHODS) and all(positive(v) for v in sealed.values())
            and sealed == estimates, "sealed predictions differ from the reference calibration and work counters")
    native, limits = row["native_samples_ns"], row["fixed_limits_ms"]
    samples(native, 9)
    require(isinstance(limits, list) and limits and all(positive(ms) for ms in limits)
            and limits == sorted(set(limits)), "expected distinct, sorted, positive whole-millisecond limits")
    oracle = native_range(native)
    errors = {}
    for method, estimate in estimates.items():
        errors[method] = {key: round(MS * Fraction(estimate - oracle[key], oracle[key]))
                          for key in ("min_ns", "median_ns", "max_ns")}
    return dict(reference_elf_sha256=row["reference_elf_sha256"], submission_elf_sha256=row["submission_elf_sha256"],
                reference_cpu=native_range(row["reference_samples_ns"]), native_cpu=oracle, predictions=estimates,
                signed_error_ppm=errors,
                fixed_deadlines={k: deadlines(v, native, limits) for k, v in estimates.items()})


def summarize(cases):
    result = {}
    for method in METHODS:
        errors = [Fraction(c["predictions"][method] - c["native_cpu"]["median_ns"], c["native_cpu"]["median_ns"])
                  for c in cases]
        points = [p for c in cases for p in c["fixed_deadlines"][method]]
        unanimous = [p for p in points if not p["mixed"]]
        result[method] = dict(cases=len(cases), median_absolute_error_ppm=round(MS * median(map(abs, errors))),
                              maximum_absolute_error_ppm=round(MS * max(map(abs, errors))),
                              within_20pct=sum(abs(e) * 5 <= 1 for e in errors),
                              fixed_limits=len(points), mixed=len(points) - len(unanimous),
                              majority_correct=sum(p["majority_correct"] for p in points),
                              unanimous_correct=sum(p["majority_correct"] for p in unanimous),
                              false_tle=sum(not p["majority_correct"] and p["model_tle"] for p in unanimous),
                              false_accept=sum(not p["majority_correct"] and not p["model_tle"] for p in unanimous))
    return result


def evaluate(cases):
    """Score already bound input/ELF pairs and nine independent native submission timings per input."""
    require(isinstance(cases, dict) and cases, "expected measured cases")
    roles = {role: {row[role + "_elf_sha256"] for row in cases.values()} for role in ("reference", "submission")}
    require(all(isinstance(d, str) and re.fullmatch("[0-9a-f]{64}", d) for role in roles.values() for d in role)
            and roles["reference"].isdisjoint(roles["submission"]), "reference and submission ELFs must be disjoint")
    scored = {}
    programs = defaultdict(list)
    for name, row in cases.items():
        problem, sep, variant = name.partition("/")
        require(sep and problem and variant, "expected problem/input case identifier")
        scored[name] = score_case(row)
        programs[problem].append(scored[name])
    return dict(schema=1, authority="diagnostic_only", all=summarize(list(scored.values())),
                programs={p: summarize(rows) for p, rows in programs.items()}, cases=scored)
