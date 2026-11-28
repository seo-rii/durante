"""Verify three incompatible reference-relative verdicts using original GCE archives."""
import argparse
from fractions import Fraction
import json
from pathlib import Path
import runpy

HERE = Path(__file__).resolve().parent
CAP = HERE.parent
string = runpy.run_path(str(CAP / "string_matching/replay.py"))
order = string["clock"]["previous"]
read, require = string["read"], string["require"]
FEATURES = ("instructions", "latency_x16", "rep_bytes", "l1d_misses", "random_2m", "random_32m",
            "stream_2m", "l1d_dependent", "dependent_2m", "gshare_misses", "recurrence_x16", "intercept")
# Study, solver, registered reference, deadline factor, native decision, multiplier.
CASES = (
    ("string_matching", "random-naive", "random-kmp", 1, "accept", 180),
    ("string_matching", "periodic-naive", "periodic-kmp", 3, "timeout", 1),
    ("order_statistics_transfer", "shuffled-segtree", "shuffled-merge", 1, "accept", 1),
)


def signed_row(candidate, reference, times, ref_times, factor, expected):
    require(len(candidate) == len(reference) == len(FEATURES) and
            all(type(v) is int and v >= 0 for v in candidate) and
            all(type(v) is int and v > 0 for v in reference), "invalid or zero reference feature")
    low, high = Fraction(min(times), max(ref_times)), Fraction(max(times), min(ref_times))
    require((expected == "accept" and high <= factor) or (expected == "timeout" and low > factor),
            "native outcome is mixed or disagrees with the certificate")
    sign = 1 if expected == "timeout" else -1
    return dict(native_range=[str(low), str(high)], expected=expected,
                row=[sign * (s - factor * r) for s, r in zip(candidate, reference, strict=True)])


def certificate(rows):
    require(rows and all(type(r["multiplier"]) is int and r["multiplier"] > 0 for r in rows)
            and any(r["expected"] == "timeout" for r in rows), "positive multiplier on a strict verdict required")
    combined = [sum(r["multiplier"] * value for r, value in zip(rows, column, strict=True))
                for column in zip(*(r["row"] for r in rows), strict=True)]
    require(len(combined) == len(FEATURES) and all(v <= 0 for v in combined),
            "weighted sum does not prove impossibility")
    return combined


def observations(folder, plan, reg, module, phase, host, names):
    protocol = plan["protocol"]
    clients = {arm: plan["clients"][client] for arm, client in protocol["clients"].items()}
    samples, _ = order["read_boot"](phase, host, reg, plan, evidence=folder / "evidence",
        freeze=module["FREEZE"], client_by_arm=clients,
        diagnostic_by_arm={"outer": "outer", "flat": "outer", "branch": "latency"})
    order["read_branch"](folder / "evidence" / (phase + ".tar.gz"), samples, include_recurrence=True)
    result = {}
    for name in names:
        arms = samples[name]
        caches = [r["counters"] for arm in ("flat", "outer") for r in arms[arm]]
        require(all(c == caches[0] for c in caches), "cache repetitions or clients disagree")
        vectors = []
        for sample in arms["branch"]:
            require(all(caches[0][key] == value for key, value in sample["counters"].items()),
                    "different measured work")
            features = dict(order["ml4_features"](caches[0]), gshare_misses=sample["branch"]["gshare_mispredicts"],
                            recurrence_x16=sample["loop_recurrence_cycles_x16"], intercept=1)
            vectors.append([features[k] for k in FEATURES])
        require(len(vectors) == protocol["rounds"].count("branch") and
                all(type(v) is int and v >= 0 for row in vectors for v in row) and
                all(row == vectors[0] for row in vectors), "unstable or incomplete predictor counts")
        result[name] = (vectors[0], [r["cpu_us"] for r in arms["native"]])
    return result


def replay():
    boots = {phase: [] for phase in ("primary", "repeat")}
    hosts = []
    for study, module in (("string_matching", string), ("order_statistics_transfer", order)):
        folder = CAP / study
        reg, plan, *_ = module["registered"]()
        cases = [c for c in CASES if c[0] == study]
        names = {name for _, solver, ref, *_ in cases for name in (solver, ref)}
        groups = plan["protocol"]["groups"].values()
        receipts = read(folder / "evidence/cloud.json")
        for phase, rows in boots.items():
            host = receipts[phase]
            hosts.append(host)
            measured = observations(folder, plan, reg, module, phase, host, names)
            for _, solver, ref, factor, expected, multiplier in cases:
                require(any(group[0] == ref and solver in group[1:] for group in groups),
                        "incorrect reference for this solver and input")
                vector, times = measured[solver]
                anchor, ref_times = measured[ref]
                r = signed_row(vector, anchor, times, ref_times, factor, expected)
                rows.append(dict(study=study, solver=solver, reference=ref, factor=factor, multiplier=multiplier,
                                 candidate_features=vector, reference_features=anchor, **r))
    for key in ("instance_id", "boot_disk_id", "boot_sha256"):
        require(len({host[key] for host in hosts}) == 4, "reused guest or disk")
    return dict(features=list(FEATURES), boots={phase: dict(rows=rows, combined=certificate(rows))
                                               for phase, rows in boots.items()})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
    result = replay()
    if args.verify:
        require(result == read(args.verify), "published certificate differs from the original archives")
        print("Verified three decisive inequalities and their integer contradiction on each boot set.")
    else:
        print(json.dumps(result, indent=2))
