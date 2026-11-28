"""Replay the predeclared loop-recurrence increment on seven archived Milan panels."""
import argparse
from collections import defaultdict
from fractions import Fraction
from hashlib import sha256
import json
from pathlib import Path
import runpy
from statistics import median
import tarfile

HERE = Path(__file__).resolve().parent
CAP = HERE.parent
offset = runpy.run_path(str(CAP / "score_offset/replay.py"))
lcs = runpy.run_path(str(CAP / "lcs_layout/replay.py"))
read, require = lcs["read"], lcs["require"]
BOOTS = ("primary", "repeat")
LIMITS = offset["LIMITS"]
COUNTER = "loop_recurrence_cycles_x16"
MILAN_RECORD_SHA = "574b5a7033fdf966ade97ffb05f66332fd346d4bf169edbddaa20a6c605f4ab6"


def stable_counts(samples, names, repetitions):
    require(set(samples) == set(names), "recurrence observations omit or add an executable")
    counts = {}
    for name, values in samples.items():
        require(len(values) == repetitions and all(type(v) is int and v >= 0 for v in values),
                "missing or invalid recurrence observation: " + name)
        require(len(set(values)) == 1, "recurrence count varies: " + name)
        counts[name] = values[0]
    return counts


def recurrence(study):
    folder = CAP / study
    plan = read(folder / "frozen.json")
    if study == "milan":
        source = folder / "gshare/records.json"
        require(sha256(source.read_bytes()).hexdigest() == MILAN_RECORD_SHA, "different legacy predictor archive")
        samples = defaultdict(list)
        # Original guests predate the predictor. Their two local registered
        # repeats used these same ELFs and inputs, checked by original_panel.
        for row in read(source)["rows"]:
            samples[row["case"]].append(row["features"][COUNTER])
        observed = stable_counts(samples, plan["cases"], 2)
        return {phase: observed for phase in BOOTS}
    evidence = folder / "evidence"
    by_phase = {}
    receipts = read(evidence / "cloud.json")
    for phase in BOOTS:
        path = evidence / (phase + ".tar.gz")
        require(sha256(path.read_bytes()).hexdigest() == receipts[phase]["raw_sha256"], "changed raw guest archive")
        samples = defaultdict(list)
        expected_files = set()
        with tarfile.open(path) as tar:
            for row in map(json.loads, tar.extractfile("output/trials.jsonl")):
                if row["arm"] == "branch":
                    name = f"output/branch-{row['index']:03d}.json"
                    expected_files.add(name)
                    samples[row["case"]].append(json.load(tar.extractfile(name))[COUNTER])
            actual = [name for name in tar.getnames() if name.startswith("output/branch-")]
            require(sorted(actual) == sorted(expected_files), "different indexed predictor records")
        by_phase[phase] = stable_counts(samples, plan["cases"], plan["protocol"]["rounds"].count("branch"))
    return by_phase


def cases():
    # These readers validate the ELF/input identities, meter receipts, schedule,
    # native CPU, cache counters and branch scores from their original evidence.
    yield from offset["cases"]()
    for study in ("lcs_layout", "maxsub_floor"):
        path = CAP / study
        module = lcs if study == "lcs_layout" else runpy.run_path(str(path / "replay.py"))
        reg, plan = module["registered"]()
        panels, _, _ = lcs["measured_panels"](path, plan, reg, module["FREEZE"])
        for phase in BOOTS:
            panel = panels[phase]
            for ref, *solvers in plan["protocol"]["groups"].values():
                anchor, ref_scores = panel[ref]
                ref_score, = set(ref_scores)
                for solver in solvers:
                    native, scores = panel[solver]
                    score, = set(scores)
                    yield dict(study=study, family=study, boot=phase, solver=solver, reference=ref,
                               truth=(min(native) / max(anchor), max(native) / min(anchor)),
                               native_ratio=median(native) / median(anchor), score=score, ref_score=ref_score)


def score(row, solver_charge, reference_charge):
    ratio = (row["score"] + solver_charge) / (row["ref_score"] + reference_charge)
    outcomes = [offset["archive"]["decision"](row["truth"], ratio, limit) for limit in LIMITS]
    return dict(prediction=str(ratio), error=str(ratio / row["native_ratio"] - 1), outcomes=outcomes)


def summary(rows):
    models = {name: offset["summarize"](rows, name) for name in ("baseline", "recurrence")}
    changes = dict(new_false_decisions=[], corrected_decisions=[])
    for row in rows:
        for limit, old, new in zip(LIMITS, row["baseline"]["outcomes"], row["recurrence"]["outcomes"], strict=True):
            if new != old:
                transition = "new_false_decisions" if old == "correct" else "corrected_decisions"
                changes[transition].append([row["study"], row["solver"], str(limit), old, new])
    return dict(**models, **changes, meets_criterion=not changes["new_false_decisions"] and
                Fraction(models["recurrence"]["median_abs_error"]) < Fraction(models["baseline"]["median_abs_error"]))


def replay():
    protocol = read(HERE / "protocol.json")
    weight = Fraction(protocol["coefficient_ns"])
    require(weight == Fraction(10, 496) and tuple(map(Fraction, protocol["deadlines"])) == LIMITS,
            "different registered charge or deadline")
    counters = {study: recurrence(study) for study in protocol["panels"]}
    hosts = [host for study in counters for host in read(CAP / study / "evidence/cloud.json").values()]
    for key in ("boot_sha256", "instance_id", "boot_disk_id"):
        require(len({host[key] for host in hosts}) == 2 * len(counters), "reused guest or disk")
    seen = defaultdict(set)
    rows = defaultdict(lambda: defaultdict(list))
    for row in cases():
        study, phase = row["study"], row["boot"]
        target, ref = row["solver"], row["reference"]
        seen[study, phase].update((target, ref))
        measured = counters[study][phase]
        rows[row["family"]][phase].append(dict(study=study, solver=target, reference=ref,
            native_ratio=str(row["native_ratio"]), native_range=list(map(str, row["truth"])),
            solver_score_ns=str(row["score"]), reference_score_ns=str(row["ref_score"]),
            solver_recurrence=measured[target], reference_recurrence=measured[ref],
            baseline=score(row, 0, 0), recurrence=score(row, weight * measured[target], weight * measured[ref])))
    require(all(seen[study, phase] == set(counters[study][phase]) for study in counters for phase in BOOTS),
            "missing problem or executable in the comparison")
    families = {family: {phase: summary(cases) for phase, cases in boots.items()} for family, boots in rows.items()}
    pooled = {phase: summary([row for family in rows.values() for row in family[phase]]) for phase in BOOTS}
    return dict(passes_both_boots=all(g["meets_criterion"] for g in pooled.values()), pooled=pooled,
                families=families, cases=rows)


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--verify", type=Path)
    args = cli.parse_args()
    result = replay()
    if args.verify:
        require(result == read(args.verify), "different published recurrence results")
        print("Verified every registered solver and recurrence count, both boot panels and all deadline decisions.")
    else:
        print(json.dumps(result, indent=2))
