"""Calibrate a common score offset on literal matching and test historical transfer."""
import argparse
from collections import Counter, defaultdict
from fractions import Fraction
import json
from pathlib import Path
import runpy
from statistics import median
import sys

HERE = Path(__file__).resolve().parent
CAPACITY = HERE.parent
sys.path.insert(0, str(CAPACITY / "allocator_transfer"))
archive = runpy.run_path(str(CAPACITY / "allocator_transfer/replay.py"))
LIMITS = archive["LIMITS"]
read = archive["read"]
MODELS = ("point", "global_offset", "reference_floor")


def crossing(solver, reference, native_ratio):
    """Offset that makes (solver+c)/(reference+c) equal this native ratio."""
    if native_ratio == 1:
        return None
    return Fraction(solver - native_ratio * reference, native_ratio - 1)


def cases():
    graph = runpy.run_path(str(CAPACITY / "dijkstra_relaxation/replay.py"))
    clock = graph["clocks"]
    string = runpy.run_path(str(CAPACITY / "string_matching/replay.py"))
    reg, plan = string["registered"]()
    weights = plan["ml4"]
    for study in ("order_statistics_transfer", "range_distinct", "dijkstra_relaxation"):
        if read(CAPACITY / study / "frozen.json")["ml4"] != weights:
            raise ValueError("different ML4 weights in " + study)
    if read(CAPACITY / "milan/frozen.json")["weights"]["dijkstra"]["ML4"] != weights:
        raise ValueError("different original weights")
    if Fraction(str(plan["gshare_miss_ns"])) != archive["PENALTY"]:
        raise ValueError("different branch miss penalty")
    groups = [("milan", *g) for g in archive["original_panel"](clock, fixed_weight_group="dijkstra")]
    groups.extend(archive["new_panels"](clock, graph))
    groups.extend(archive["registered_panel"]("string_matching", string, plan, reg, clock))
    for study, family, boot, (ref, *solvers), samples in groups:
        if family == "dijkstra_relaxation":
            family = "dijkstra"  # the historical and layered inputs share solver ELFs
        native_ref, _, ref_score = samples[ref]
        for solver in solvers:
            native, _, score = samples[solver]
            yield dict(study=study, family=family, boot=boot, solver=solver, reference=ref,
                       truth=(Fraction(min(native), max(native_ref)), Fraction(max(native), min(native_ref))),
                       native_ratio=Fraction(median(native), median(native_ref)), score=score, ref_score=ref_score)


def fit_offset(rows):
    crossings = {}
    for r in rows:
        key = "/".join((r["family"], r["boot"], r["solver"]))
        if key in crossings:
            raise ValueError("duplicate calibration case: " + key)
        crossings[key] = crossing(r["score"], r["ref_score"], r["native_ratio"])
    eligible = [v for v in crossings.values() if v is not None and v > 0]
    if not eligible:
        raise ValueError("no finite positive score-offset crossings")
    offset = round(median(eligible) / 1_000_000) * 1_000_000
    return offset, {k: str(v) if v is not None else None for k, v in crossings.items()}


def floor_offset(reference, threshold):
    return max(0, threshold - reference)


def score_case(row, offset):
    prediction = Fraction(row["score"] + offset, row["ref_score"] + offset)
    outcomes = [archive["decision"](row["truth"], prediction, limit) for limit in LIMITS]
    return dict(applied_offset_ns=str(offset), prediction=str(prediction), error=str(prediction / row["native_ratio"] - 1),
                outcomes=outcomes)


def summarize(cases, model):
    records = [case[model] for case in cases]
    errors = [abs(Fraction(r["error"])) for r in records]
    outcomes = Counter(outcome for r in records for outcome in r["outcomes"])
    return dict(decisions=outcomes, comparisons=len(errors), within_20=sum(e <= Fraction(1, 5) for e in errors),
                median_abs_error=str(median(errors)), worst_abs_error=str(max(errors)))


def replay():
    protocol = read(HERE / "protocol.json")
    rows = list(cases())
    development = protocol["development_problem"]
    offset, crossings = fit_offset(r for r in rows if r["family"] == development)
    if offset != protocol["offset_ns"] or list(map(Fraction, protocol["deadlines"])) != list(LIMITS):
        raise ValueError("different development fit or deadlines")
    threshold = protocol["exploratory_reference_floor"]["threshold_ns"]
    groups = defaultdict(list)
    for r in rows:
        case = {k: r[k] for k in ("study", "solver", "reference")}
        case.update(native_ratio=str(r["native_ratio"]), native_range=list(map(str, r["truth"])),
                    solver_score_ns=str(r["score"]), reference_score_ns=str(r["ref_score"]),
                    point=score_case(r, 0), global_offset=score_case(r, offset),
                    reference_floor=score_case(r, floor_offset(r["ref_score"], threshold)))
        groups[r["family"], r["boot"]].append(case)
    summaries = defaultdict(dict)
    details = defaultdict(dict)
    for (family, boot), group in groups.items():
        summaries[family][boot] = {m: summarize(group, m) for m in MODELS}
        details[family][boot] = group
    transfer = {}
    for phase in ("primary", "repeat"):
        subset = [r for (family, boot), group in groups.items()
                  if boot == phase and family != protocol["development_problem"] for r in group]
        transfer[phase] = {m: summarize(subset, m) for m in MODELS}
    folds = {}
    for held_out in sorted(summaries):
        training = [r for r in rows if r["family"] != held_out]
        fitted, _ = fit_offset(training)
        boots = {}
        for boot in ("primary", "repeat"):
            held = [{"fitted": score_case(r, fitted)} for r in rows
                    if r["family"] == held_out and r["boot"] == boot]
            boots[boot] = summarize(held, "fitted")
        folds[held_out] = dict(offset_ns=fitted, training_families=sorted({r["family"] for r in training}), boots=boots)
    return dict(offset_ns=offset, development_crossings_ns=crossings, transfer=transfer,
                exploratory_leave_one_family_out=folds, families=summaries, cases=details)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
    result = replay()
    if args.verify:
        if read(args.verify) != result:
            raise SystemExit("published score-offset results differ")
        print("Verified the development crossings and archived relative time predictions.")
    else:
        print(json.dumps(result, indent=2))
