"""Replay the four frozen relative clocks and three-model unanimity on a held-out problem."""
import argparse
from collections import Counter
from fractions import Fraction
import json
from pathlib import Path
import runpy
from statistics import median
import sys

HERE = Path(__file__).resolve().parent
PARENT = HERE.parent / "order_statistics_transfer"
new_inputs = runpy.run_path(str(HERE / "inputs.py"))
sys.path.insert(0, str(PARENT))
previous = runpy.run_path(str(PARENT / "replay.py"))
read, require, sha = previous["read"], previous["require"], previous["sha"]
milan = previous["milan"]
rn = previous["r6"]
FREEZE = "d740a9eddf8d18bc95d78ab13e4a4e569fae61c1"
REG = "3d88bcbffbad369882f3bf505c2f9d9b15fe9beaa6b338620cb6b8c693cbd18a"
CANDIDATES = ("ML4", "ML4_plus_misses", "dependent", "ML4_plus_3_3")
VOTERS = CANDIDATES[:3]


def registered():
    protocol = read(HERE / "protocol.json")
    plan = read(HERE / "frozen.json")
    reg = read(HERE / "registration.json")
    files = reg["payload"]
    require(sha((HERE / "registration.json").read_bytes()) == REG and
            sha((HERE / "frozen.json").read_bytes()) == files["plan.json"] and protocol == plan["protocol"] and
            reg["expected_rows"] == {".": 78}, "different experiment registration")
    old_files = read(PARENT / "registration.json")["payload"]
    old_plan = read(PARENT / "frozen.json")
    require(all(h == old_files[n] for n, h in files.items() if n != "plan.json" and not n.startswith(("bin/", "inputs/")))
            and plan["ml4"] == old_plan["ml4"] and plan["clients"] == old_plan["clients"]
            and plan["gshare_miss_ns"] == old_plan["gshare"]["gshare_mispredicts"] == 6.92658480142496,
            "changed instrumentation or pre-existing weights")
    require(tuple(protocol["models"]) == CANDIDATES and tuple(protocol["unanimity_models"]) == VOTERS
            and protocol["factors"] == [1, 1.25, 1.5, 2, 3], "changed candidate, voter or deadline")
    for filename, digest in plan["source_sha256"].items():
        require(sha((HERE / filename).read_bytes()) == digest, "different source: " + filename)
    for order, names in protocol["groups"].items():
        data, queries = new_inputs["workload"](order)
        stdin = sha(new_inputs["encode"](data, queries))
        answer = sha(new_inputs["oracle"](data, queries))
        for n in names:
            c = plan["cases"][n]
            require(c["expected_sha256"] == answer and c["stdin_sha256"] == files[c["stdin"]] == stdin and
                    c["elf_sha256"] == files["bin/" + n] == files["bin/shuffled-" + n.split("-")[1]], "wrong input, ELF or oracle")
    return plan, reg


def measured(arms, weights, original_penalty):
    # Both cache clients observe the same features; use the faster flat arm to score.
    flat = [r["counters"] for r in arms["flat"]]
    outer = [r["counters"] for r in arms["outer"]]
    branch = [r["counters"] for r in arms["branch"]]
    cache_match = all(c == flat[0] for c in flat + outer)
    shared_match = all(all(c[k] == flat[0][k] for k in c) for c in branch)
    require(cache_match and shared_match, "flat/original cache or branch shared-count parity failed")
    base = [milan.predict(weights, previous["ml4_features"](c)) for c in flat]
    misses = [r["branch"]["gshare_mispredicts"] for r in arms["branch"]]
    times = {a: [r["cpu_us"] for r in runs] for a, runs in arms.items()}
    native_median = median(times["native"])
    costs = {arm: rn(median(t) / native_median) for arm, t in times.items() if arm != "native"}
    costs["flat_plus_branch"] = rn((median(times["flat"]) + median(times["branch"])) / native_median)
    telemetry = dict(cpu_us_median={a:median(t) for a, t in times.items()}, overhead_over_native=costs,
                     original_flat_counter_parity=cache_match, shared_work_parity=shared_match,
                     features=previous["ml4_features"](flat[0]), misses=[min(misses), max(misses)])
    scores = {"ML4": base,
              "dependent": [c["latency_x16"] + 700 * c["outer"][13] for c in flat]}
    for model, penalty in (("ML4_plus_misses", original_penalty), ("ML4_plus_3_3", Fraction(33, 10))):
        scores[model] = [b + penalty * m for b in base for m in misses]
    return scores, telemetry


def unanimity(verdicts):
    votes = [verdicts[m] for m in VOTERS]
    if "indeterminate" in votes:
        return "abstain_mixed_model"
    if all(v == votes[0] for v in votes):
        return votes[0]
    return "abstain_disagreement"


def summarize(decisions):
    totals = {m: Counter() for m in CANDIDATES}
    consensus = Counter(all_cells=0, native_decisive=0, issued=0, issued_native_decisive=0,
                        correct=0, false_accept=0, false_timeout=0, issued_native_mixed=0,
                        abstain_mixed_model=0, abstain_disagreement=0)
    changes = {m: dict(gained=[], lost=[]) for m in CANDIDATES[1:]}
    for case, factors in decisions.items():
        for factor, row in factors.items():
            native = row["native"]
            native_decisive = native != "indeterminate"
            consensus["all_cells"] += 1
            consensus["native_decisive"] += native_decisive
            for model in CANDIDATES:
                verdict = row[model]
                if not native_decisive:
                    outcome = "native_mixed"
                elif verdict == "indeterminate":
                    outcome = "model_mixed"
                else:
                    outcome = previous["gshare"]["outcome"](native, verdict)
                totals[model][outcome] += 1
                if native_decisive and model != "ML4" and (row["ML4"] == native) != (verdict == native):
                    changes[model]["gained" if verdict == native else "lost"].append([case, factor])
            vote = row["unanimous"]
            if vote.startswith("abstain_"):
                consensus[vote] += 1
            else:
                consensus["issued"] += 1
                consensus["issued_native_decisive"] += native_decisive
                if not native_decisive:
                    consensus["issued_native_mixed"] += 1
                elif vote == native:
                    consensus["correct"] += 1
                else:
                    consensus["false_" + vote] += 1
    consensus["coverage_all"] = rn(Fraction(consensus["issued"], consensus["all_cells"]))
    consensus["coverage_native_decisive"] = (rn(Fraction(consensus["issued_native_decisive"], consensus["native_decisive"]))
                                             if consensus["native_decisive"] else None)
    return dict(outcomes=totals, unanimity=consensus, versus_ML4=changes)


def classify(panel, plan):
    weights = {k: Fraction(str(v)) * 10**9 for k, v in plan["ml4"].items()}
    scores, telemetry = {}, {}
    for n, arms in panel.items():
        scores[n], telemetry[n] = measured(arms, weights, Fraction(str(plan["gshare_miss_ns"])))
    decisions, timing = {}, {}
    errors = {m: [] for m in CANDIDATES}
    for ref, *names in plan["protocol"]["groups"].values():
        baseline = [r["cpu_us"] for r in panel[ref]["native"]]
        for n in names:
            native = [r["cpu_us"] for r in panel[n]["native"]]
            observed = Fraction(median(native)) / Fraction(median(baseline))
            row = dict(native_median_ratio=rn(observed), native_ratio_bounds=list(map(rn, previous["ratio"](native, baseline))), models={})
            for m in CANDIDATES:
                score, reference = scores[n][m], scores[ref][m]
                ratio = Fraction(median(score)) / Fraction(median(reference))
                error = ratio / observed - 1
                row["models"][m] = dict(ratio=rn(ratio), signed_relative_error=rn(error))
                errors[m].append(abs(error))
            timing[n] = row
            decisions[n] = {}
            for factor in plan["protocol"]["factors"]:
                f = Fraction(str(factor))
                d = {m:milan.relative_verdict(scores[n][m], scores[ref][m], f) for m in CANDIDATES}
                decisions[n][str(factor)] = dict(**d, native=milan.relative_verdict(native, baseline, f), unanimous=unanimity(d))
    accuracy = {m:dict(within_20percent=sum(e <= Fraction(1, 5) for e in v), comparisons=len(v),
                       median_abs_relative_error=rn(median(v)), worst_abs_relative_error=rn(max(v))) for m,v in errors.items()}
    return dict(**summarize(decisions), ratio_accuracy=accuracy, decisions=decisions, timing=timing, telemetry=telemetry)


def replay():
    plan, reg = registered()
    evidence = HERE / "evidence"
    cloud = read(evidence / "cloud.json")
    for field in ("instance_id", "boot_disk_id", "boot_sha256"):
        ids = {v[field] for v in cloud.values()}
        require(len(ids) == 2 and ids.isdisjoint(v[field] for v in read(PARENT / "evidence/cloud.json").values()),
                "guest identity reused")
    clients = {a:plan["clients"][f] for a, f in plan["protocol"]["clients"].items()}
    panels, result = {}, {}
    for phase in ("primary", "repeat"):
        arms, steal = previous["read_boot"](phase, cloud[phase], reg, plan, evidence=evidence, freeze=FREEZE,
            client_by_arm=clients, diagnostic_by_arm={"outer":"outer", "flat":"outer", "branch":"latency"})
        previous["read_branch"](evidence / (phase + ".tar.gz"), arms)
        panels[phase] = arms
        result[phase] = dict(steal_ticks=steal, **classify(arms, plan))
    pooled = {n:{a:runs + panels["repeat"][n][a] for a, runs in arms.items()} for n, arms in panels["primary"].items()}
    result["pooled"] = classify(pooled, plan)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
    out = replay()
    if args.verify:
        require(out == read(args.verify), "published range-distinct results changed")
        print("Verified both guests, all 156 executions, ordinal answer digests and four frozen clocks.")
    else:
        print(json.dumps(out, indent=2))
