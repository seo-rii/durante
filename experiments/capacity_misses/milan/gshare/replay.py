"""Replay frozen gshare estimates against the two registered Milan native boots."""
from collections import Counter, defaultdict
from fractions import Fraction
from hashlib import sha256
import json
from pathlib import Path
import runpy
from statistics import median
import sys

HERE = Path(__file__).resolve().parent
MILAN, EXPERIMENTS = HERE.parent, HERE.parents[2]
sys.path.insert(0, str(EXPERIMENTS))
from collect_branch_model import branch_counts
import gshare_model

# Source of the existing predictor at the protocol commit.
PROBE_SOURCE_SHA = "95d943e9bbdcc9743b812540778266838a3ac25e5f819131f622f20dabd6c5a3"

milan = runpy.run_path(str(MILAN / "replay.py"))
relative_verdict, require = milan["relative_verdict"], milan["require"]


def read(path):
    return json.loads(path.read_bytes())


def frozen_weights(protocol):
    path = EXPERIMENTS / "results/cpp20-gshare-model.json"
    require(sha256(path.read_bytes()).hexdigest() == protocol["training_archive_sha256"], "training archive digest")
    fit = gshare_model.evaluate(read(path))
    require(fit["gshare_profile"] == protocol["predictor"], "wrong branch predictor")
    weights = {k: v["diagnostic_ns_per_count"] for k, v in fit["models"].items()}
    require(weights == protocol["models"], "weights differ from the original fit")
    return {k: {f: Fraction(str(w)) for f, w in values.items()} for k, values in weights.items()}


def restored(row, spec):
    for key in ("elf_sha256", "stdin_sha256"):
        require(row[key] == spec[key], "changed " + key)
    require(row["stdout_sha256"] == spec["expected_sha256"], "wrong output")
    pid = row["pid"]
    reader = milan["collector"]
    meter = reader["aggregate"]({pid: reader["parse_records"](pid, row["meter_jsonl"].encode())}, pid)
    require(meter["valid"] and meter["complete"] and meter["cpuid_profile"] == "x86-64-v2" and
            meter["process_count"] == meter["image_count"] == meter["segments"][0]["threads"] == 1 and
            meter["quality"] == "uninterrupted" and meter["processes"][0]["exit_code"] == 0 and
            meter["rep_bytes_enabled"] and meter["unsupported_rep_entries"] == 0, "invalid meter receipt")
    probe = row["features"]
    features = branch_counts(probe, meter)
    require(features["conditional_branches"] == probe["cond_branches"], "inconsistent branch totals")
    return dict(instructions=meter["instructions"], rep_bytes=meter["rep_bytes"], **features)


def ratio_range(numerators, denominators):
    return [round(float(min(numerators) / max(denominators)), 6), round(float(max(numerators) / min(denominators)), 6)]


def component_ceiling(samples, anchors):
    """Upper bound for any nonzero, nonnegative weighted sum of these counters."""
    upper = {}
    for feature in anchors[0]:
        v = max(s[feature] for s in samples)
        a = min(s[feature] for s in anchors)
        if a:
            upper[feature] = Fraction(v, a)
        elif v:
            return None
    return max(upper.values(), default=None)


def clock_scores(samples, coefficients):
    return [sum(sample[key] * weight for key, weight in coefficients.items()) for sample in samples]


def outcome(native, clock):
    if "indeterminate" in (native, clock):
        return "indeterminate"
    if native == clock:
        return "agree"
    return "false_accept" if clock == "accept" else "false_timeout"


def evaluate(evidence):
    protocol = read(HERE / "protocol.json")
    plan = read(MILAN / "frozen.json")
    weights = frozen_weights(protocol)
    reg = read(MILAN / "registration.json")
    reg["payload"] = {f["path"]: f["sha256"] for f in reg["files"]}
    infra = read(MILAN / "evidence/cloud.json")
    require(evidence["schema"] == 1 and
            evidence["protocol_sha256"] == sha256((HERE / "protocol.json").read_bytes()).hexdigest() and
            evidence["probe_source_sha256"] == PROBE_SOURCE_SHA and
            evidence["tools_sha256"]["meter"] == reg["payload"]["probe.so"] and
            evidence["tools_sha256"]["drrun"] == reg["payload"]["dynamorio/bin64/drrun"], "changed collection identity")
    names = list(plan["cases"])
    order = [(i, n) for i, cases in enumerate((names, names[::-1]), 1) for n in cases]
    require([(r["repetition"], r["case"]) for r in evidence["rows"]] == order, "trial order/coverage")
    measured = defaultdict(list)
    for row in evidence["rows"]:
        measured[row["case"]].append(restored(row, plan["cases"][row["case"]]))
    boots = {phase: milan["read_boot"](MILAN / "evidence" / (phase + ".tar.gz"), phase, plan, reg,
                                       infra[phase])[0] for phase in ("primary", "repeat")}
    cases, decisions, totals = {}, [], defaultdict(lambda: defaultdict(Counter))
    for problem, (anchor, *variants) in plan["protocol"]["groups"].items():
        for name in (anchor, *variants):
            counters = {k: [r[k] for r in measured[name]] for k in measured[name][0]}
            anchors = {k: [r[k] for r in measured[anchor]] for k in measured[anchor][0]}
            native = {phase: data[name]["native"] for phase, data in boots.items()}
            references = {phase: data[anchor]["native"] for phase, data in boots.items()}
            native["combined"] = native["primary"] + native["repeat"]
            references["combined"] = references["primary"] + references["repeat"]
            estimates = {model: (clock_scores(measured[name], coeff), clock_scores(measured[anchor], coeff))
                         for model, coeff in weights.items()}
            gce = [probe for data in boots.values() for probe in data[name]["probe"]]
            drift = max(abs(local / remote["instructions"] - 1) for local in counters["instructions"] for remote in gce)
            require(all(value == remote["movs_bytes"] + remote["stos_bytes"] for value in counters["rep_bytes"] for remote in gce),
                    "local and GCE REP totals differ")
            cases[name] = dict(counters=counters, counter_ratio={k: ratio_range(v, anchors[k]) if min(anchors[k]) else None
                                                                 for k, v in counters.items()},
                               frozen_ratio={k: ratio_range(*v) for k, v in estimates.items()},
                               native_ratio={p: ratio_range(v, references[p]) for p, v in native.items()},
                               native_median_ratio={p: round(median(v) / median(references[p]), 6) for p, v in native.items()},
                               max_instruction_drift_ppm=round(drift * 1_000_000, 5), max_weighted_ratio=None)
            ceiling = component_ceiling(measured[name], measured[anchor])
            if ceiling is not None:
                cases[name]["max_weighted_ratio"] = round(float(ceiling), 6)
            if name == anchor:
                continue
            for factor in plan["protocol"]["factors"]:
                truth = {p: relative_verdict(v, references[p], factor) for p, v in native.items()}
                clocks = {m: relative_verdict(*v, Fraction(str(factor))) for m, v in estimates.items()}
                decisions.append(dict(case=name, factor=factor, native=truth, clocks=clocks))
                for boot, actual in truth.items():
                    for model, clock in clocks.items():
                        totals[boot][model][outcome(actual, clock)] += 1
    # Post-hoc check on the known set/heap failure. ML4 is the unchanged scalar score
    # from the earlier Milan study. No coefficient inside ML4 is refitted.
    def ml4_scores(name):
        return [milan["predict"](plan["weights"]["dijkstra"]["ML4"], milan["features"](probe))
                for boot in boots.values() for probe in boot[name]["probe"]]
    anchor, name = "contest-dijkstra", "dijkstra--set_decrease_key"
    ml4_range = ratio_range(ml4_scores(name), ml4_scores(anchor))
    extra = cases[name]["counter_ratio"]
    augmented = max(ml4_range[1], extra["conditional_branches"][1], extra["gshare_mispredicts"][1])
    return dict(cases=cases, outcomes=totals, decisions=decisions,
                set_heap_ml4_ratio=ml4_range, set_heap_ml4_plus_branch_upper=augmented)


if __name__ == "__main__":
    print(json.dumps(evaluate(read(HERE / "records.json")), indent=2, sort_keys=True))
