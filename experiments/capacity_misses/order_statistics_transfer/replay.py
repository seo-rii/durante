"""Verify the registered measurements and replay independently anchored clock scores."""
import argparse
from collections import Counter
from fractions import Fraction
import json
from pathlib import Path
import runpy
from statistics import median
import sys
import tarfile

import prepare

HERE, CAP = prepare.HERE, prepare.CAP
# Load the original #119 implementation before the capacity study prepends
# its own collector module directory to sys.path.
gshare = runpy.run_path(str(prepare.GSHARE / "replay.py"))
sys.path.insert(0, str(CAP / "graph_stage_cost"))
from graph_evidence import milan, read_boot, require, sha

FREEZE = "b410405414138ea67f79d40ed7d8a6db1541aa3c"
REG = "8b98f6cf9583586730a9db08b5398f4b8fe7f925404bd9827cfa7771fb16c459"
PHASES = ("primary", "repeat")


def read(path):
    return json.loads(path.read_bytes())


def registered():
    reg, plan = read(HERE / "registration.json"), read(HERE / "frozen.json")
    manifest = reg["payload"]
    protocol = read(HERE / "protocol.json")
    require(sha((HERE/"registration.json").read_bytes()) == REG and
            sha((HERE/"frozen.json").read_bytes()) == manifest["plan.json"] and protocol == plan["protocol"] and
            reg["expected_rows"] == {".":144}, "incorrect registration")
    old = read(prepare.PRED / "frozen.json")
    require(all(plan["cases"][k] == v for k,v in old["cases"].items())
            and all(g["ML4"] == plan["ml4"] for g in old["weights"].values()), "different original panel or weights")
    for filename, digest in plan["source_sha256"].items():
        require(sha((HERE / filename).read_bytes()) == digest, "different study source")
    for name, case in plan["cases"].items():
        require(manifest["bin/"+name] == case["elf_sha256"] and manifest[case["stdin"]] == case["stdin_sha256"],
                "different binary or input")
    for name, h in plan["clients"].items():
        require(manifest[name] == h, "changed client")
    require(plan["clients"]["flat.so"] == read(prepare.FLAT/"frozen.json")["clients"]["flat.so"] and
            plan["clients"]["branch.so"] == read(prepare.GSHARE/"records.json")["tools_sha256"]["probe"] and
            plan["clients"]["original.so"] == old["probe_sha256"], "different diagnostic lineage")
    weights = gshare["frozen_weights"](read(prepare.GSHARE / "protocol.json"))["with_gshare"]
    require(weights == {k: Fraction(str(v)) for k,v in plan["gshare"].items()}, "changed independent branch fit")
    return reg, plan, weights


def read_branch(evidence, arms, *, include_recurrence=False):
    with tarfile.open(evidence, "r:gz") as archive:
        iters = {n: iter(modes["branch"]) for n,modes in arms.items()}
        for row in map(json.loads, archive.extractfile("output/trials.jsonl")):
            if row["arm"] != "branch":
                continue
            sample = next(iters[row["case"]])
            info = json.load(archive.extractfile(f"output/branch-{row['index']:03d}.json"))
            branch = gshare["branch_counts"](info, sample["counters"])
            require(branch["conditional_branches"] == info["cond_branches"], "branch tally mismatch")
            if include_recurrence:
                cycles = info.get("loop_recurrence_cycles_x16")
                require(type(cycles) is int and cycles >= 0, "invalid loop recurrence counter")
                sample["loop_recurrence_cycles_x16"] = cycles
            sample["branch"] = branch


def ml4_features(c):
    v = c["outer"]
    return milan.features(dict(instructions=c["instructions"], movs_bytes=c["movs_bytes"], stos_bytes=c["stos_bytes"],
        latency_bound_x16=c["latency_x16"], l1d_misses=c["l1d_misses"], l1d_dependent=v[13],
        outer_misses=v[4:7], outer_stream=v[7:10], outer_dependent=v[10:13]))


def scores(arms, weights, branch):
    fulls = [v["counters"] for a in ("outer", "flat") for v in arms[a]]
    comparable = [r["counters"] for r in arms["branch"]]
    require(all(r == fulls[0] for r in fulls), "original and optimized full diagnostics disagree")
    require(all(all(r[k] == fulls[0][k] for k in r) for r in comparable), "shared work counters changed")
    original = [milan.predict(weights, ml4_features(r["counters"])) for r in arms["flat"]]
    bscores, misses = [], []
    for r in arms["branch"]:
        record = dict(**r["branch"], instructions=r["counters"]["instructions"], rep_bytes=r["counters"]["rep_bytes"])
        ns = sum(coef * record[key] for key,coef in branch.items())
        bscores.append(ns)
        misses.append(record["gshare_mispredicts"])
    penalty = branch["gshare_mispredicts"]
    return {"ML4": original, "gshare": bscores, "ML4_plus_misses": [s + penalty*m for s in original for m in misses]}


def ratio(values, refs):
    return (Fraction(min(values),max(refs)), Fraction(max(values),min(refs)))


def r6(v):
    return round(float(v),6)


def classify(rows, plan, branch):
    weights = {k: Fraction(str(v)) * 10**9 for k,v in plan["ml4"].items()}
    proto = plan["protocol"]
    estimates = {n:scores(a,weights,branch) for n,a in rows.items()}
    summary = {}
    for key, groups in (("predecessor", proto["training_examples"]), ("inversions", proto["holdout"])):
        outcomes = {m:Counter() for m in proto["models"]}
        errors = {m:[] for m in proto["models"]}
        timing, verdicts = {}, {}
        lost, gained = [], []
        for group in groups:
            anchor, *variants = proto["groups"][group]
            ref = [r["cpu_us"] for r in rows[anchor]["native"]]
            for n in variants:
                data = [r["cpu_us"] for r in rows[n]["native"]]
                native = Fraction(median(data)) / Fraction(median(ref))
                timing[n] = dict(native_median_ratio=r6(native), native_bounds=list(map(r6,ratio(data,ref))), models={})
                for model in outcomes:
                    vals, refs = estimates[n][model], estimates[anchor][model]
                    pred = median(vals)/median(refs)
                    error = pred/native - 1
                    timing[n]["models"][model] = dict(median_ratio=r6(pred), bounds=list(map(r6,ratio(vals,refs))),
                                                       signed_ratio_error=r6(error))
                    errors[model].append(abs(error))
                vd = verdicts[n] = {}
                for factor in proto["factors"]:
                    f = Fraction(str(factor))
                    actual = milan.relative_verdict(data,ref,f)
                    decisions = {model: milan.relative_verdict(estimates[n][model],estimates[anchor][model],f)
                                 for model in outcomes}
                    vd[str(factor)] = dict(native=actual, **decisions)
                    for model,label in decisions.items():
                        outcome = gshare["outcome"](actual,label)
                        outcomes[model][outcome] += 1
                    if actual != "indeterminate":
                        was = decisions["ML4"] == actual
                        now = decisions["ML4_plus_misses"] == actual
                        if was != now:
                            (lost if was else gained).append([n,factor])
        accuracy = {m:dict(median_abs_ratio_error=r6(median(v)),worst_abs_ratio_error=r6(max(v)),
                           within_20percent=sum(e <= Fraction(1,5) for e in v), comparisons=len(v))
                    for m,v in errors.items()}
        summary[key] = dict(outcomes=outcomes, ratio_accuracy=accuracy, timing=timing,
                            lost=lost, gained=gained, decisions=verdicts,
                            lower_hybrid_median_error=median(errors["ML4_plus_misses"]) < median(errors["ML4"]))
    telemetry = {}
    for n,arms in rows.items():
        cpu = {a:median([r["cpu_us"] for r in samples]) for a,samples in arms.items()}
        counts = [r["branch"] for r in arms["branch"]]
        m = [v["gshare_mispredicts"] for v in counts]
        b = [v["conditional_branches"] for v in counts]
        telemetry[n] = dict(cpu_us=cpu, overhead={a:r6(v/cpu["native"]) for a,v in cpu.items() if a != "native"},
                            two_pass_over_native=r6((cpu["flat"]+cpu["branch"])/cpu["native"]),
                            branch_misses=[min(m),max(m)], branches=[min(b),max(b)],
                            branch_repeatable=all(v == counts[0] for v in counts),
                            full_cache=ml4_features(arms["outer"][0]["counters"]))
    bounds = {}
    for group in proto["training_examples"]:
        anchor = proto["groups"][group][0]
        name = group + "-fenwick"
        base_excess = max(estimates[name]["ML4"]) - min(estimates[anchor]["ML4"])
        misses_saved = min(r["branch"]["gshare_mispredicts"] for r in rows[anchor]["branch"]) - max(
            r["branch"]["gshare_mispredicts"] for r in rows[name]["branch"])
        bound = base_excess / misses_saved if base_excess > 0 and misses_saved > 0 else None
        bounds[group] = dict(ML4_excess_ns=r6(base_excess), minimum_misses_saved=misses_saved,
                             minimum_extra_ns_per_miss=r6(bound) if bound is not None else None)
    return dict(**summary, telemetry=telemetry, fenwick_one_times=bounds)


def exploratory_interval(panels, plan):
    """After unblinding, bound one shared nonnegative penalty without selecting a fit."""
    solver = CAP / "block_events"
    sys.path.insert(0, str(solver))
    bounds = runpy.run_path(str(solver / "replay.py"))
    constraints = []
    weights = {k:Fraction(str(v))*10**9 for k,v in plan["ml4"].items()}
    for phase, data in panels.items():
        for anchor, *names in plan["protocol"]["groups"].values():
            def values(n):
                a = data[n]
                native = [r["cpu_us"] for r in a["native"]]
                score = [milan.predict(weights,ml4_features(r["counters"])) for r in a["flat"]]
                misses = [r["branch"]["gshare_mispredicts"] for r in a["branch"]]
                return native, score, misses
            ref, base, misses = values(anchor)
            for n in names:
                times, sub, event = values(n)
                for factor in plan["protocol"]["factors"]:
                    f = Fraction(str(factor))
                    native = milan.relative_verdict(times,ref,f)
                    constraints.append(bounds["Cell"](f"{phase}/{n}/{factor:g}", native,
                        (min(sub)-f*max(base),min(event)-f*max(misses)),
                        (max(sub)-f*min(base),max(event)-f*min(misses))))
    return bounds["intersect"](constraints).report()


def replay():
    reg, plan, weights = registered()
    evidence = HERE / "evidence"
    cloud = read(evidence / "cloud.json")
    for field in ("boot_sha256","instance_id","boot_disk_id"):
        ids = {d[field] for d in cloud.values()}
        require(len(ids) == 2 and ids.isdisjoint(d[field] for d in read(prepare.PRED/"evidence/cloud.json").values()),
                "guests must have independent identities")
    clients = {a:plan["clients"][n] for a,n in plan["protocol"]["clients"].items()}
    panels, results = {}, {}
    for p in PHASES:
        arms, steal = read_boot(p,cloud[p],reg,plan,evidence=evidence,freeze=FREEZE,client_by_arm=clients,
                                diagnostic_by_arm={"outer":"outer","flat":"outer","branch":"latency"})
        read_branch(evidence/(p+".tar.gz"),arms)
        panels[p] = arms
        results[p] = dict(steal_ticks=steal, **classify(arms,plan,weights))
    pooled = {n:{a:panels["primary"][n][a]+panels["repeat"][n][a] for a in v}
              for n,v in panels["primary"].items()}
    results["pooled"] = classify(pooled,plan,weights)
    fixed = results["pooled"]
    history = read(prepare.PRED/"results.json")["primary"]["timing"]
    for name, timing in fixed["predecessor"]["timing"].items():
        previous = history[name]["models"]["ML4"]["ratio"]
        require(timing["models"]["ML4"]["median_ratio"] == previous, "different historical ML4 score")
    evaluation = {}
    for phase in PHASES:
        result = results[phase]
        correction = {}
        for group in plan["protocol"]["training_examples"]:
            verdict = result["predecessor"]["decisions"][group+"-fenwick"]["1"]
            reproduced = verdict["native"] == "accept" and verdict["ML4"] == "timeout"
            correction[group] = ("fixed" if reproduced and verdict["ML4_plus_misses"] == "accept" else
                                 "unfixed" if reproduced else "original false timeout not reproduced")
        holdout = result["inversions"]
        evaluation[phase] = dict(old_fenwick=correction, holdout_no_lost_verdicts=not holdout["lost"],
                                 lower_holdout_median_error=holdout["lower_hybrid_median_error"])
    results["registered_criterion"] = dict(boots=evaluation,
        passed=all(all(x=="fixed" for x in e["old_fenwick"].values()) and e["holdout_no_lost_verdicts"]
                   and e["lower_holdout_median_error"] for e in evaluation.values()))
    results["exploratory_ns_per_miss"] = exploratory_interval({**panels, "pooled":pooled},plan)
    return results


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--verify",type=Path)
    args = cli.parse_args()
    result = replay()
    if args.verify:
        require(result == read(args.verify),"published results changed")
        print("Verified all four input groups, both placements, registered models, raw counters and CPU comparisons.")
    else:
        print(json.dumps(result,indent=2))
