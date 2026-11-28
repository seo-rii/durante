"""Verify both registered guests and score queue behavior on the paired DAGs."""
import argparse
from collections import Counter
from fractions import Fraction
import json
from pathlib import Path
import runpy
from statistics import median

HERE = Path(__file__).resolve().parent
BASE = HERE.parent / "range_distinct"
dataset = runpy.run_path(str(HERE / "inputs.py"))
clocks = runpy.run_path(str(BASE / "replay.py"))
previous = clocks["previous"]
read, require, sha = clocks["read"], clocks["require"], clocks["sha"]
round6 = clocks["rn"]
FREEZE = "1c4c3018964f717c4fac8098613af29b32ce927a"
REG = "861bbe6468eb27d15730ac02359d9a3f68a78e1ac8007282b566daaf7c29d0a1"


def registered():
    reg, plan = read(HERE / "registration.json"), read(HERE / "frozen.json")
    files = reg["payload"]
    require(sha((HERE / "registration.json").read_bytes()) == REG and
            sha((HERE / "frozen.json").read_bytes()) == files["plan.json"] and
            reg["expected_rows"] == {".": 78} and plan["protocol"] == read(HERE / "protocol.json"),
            "changed registration")
    old = read(BASE / "registration.json")["payload"]
    common = lambda n: n != "plan.json" and not n.startswith(("bin/", "inputs/"))
    require({n: h for n, h in files.items() if common(n)} == {n: h for n, h in old.items() if common(n)},
            "different guest or diagnostic")
    original = read(BASE / "frozen.json")
    require(all(plan[k] == original[k] for k in ("ml4", "clients", "gshare_miss_ns")) and
            all(plan["protocol"][k] == original["protocol"][k] for k in
                ("models", "unanimity_models", "model_definitions", "factors", "modes", "clients", "rounds")),
            "changed clocks, budgets or schedule")
    for n, h in plan["source_sha256"].items():
        require(sha((HERE / n).read_bytes()) == h, "different source: " + n)
    orig = runpy.run_path(str(BASE.parent / "aonohako_shortest_paths/prepare.py"))["parent_files"]()
    names = ["contest-dijkstra", "dijkstra--vecvec_getchar_pq", "dijkstra--set_decrease_key"]
    for order, solvers in plan["protocol"]["groups"].items():
        digest = sha(dataset["encode"](order))
        for n, source in zip(solvers, names, strict=True):
            case = plan["cases"][n]
            require(case["stdin_sha256"] == files[case["stdin"]] == digest and
                    case["expected_sha256"] == sha(dataset["answer"]()) and
                    case["elf_sha256"] == files["bin/" + n] == orig["bin/" + source], "different ELF, input or answer")
    return plan, reg


def skipped_fraction(counters):
    skipped = counters["l1d_skipped"]
    total = counters["l1d_refs"] + skipped
    require(total > 0 and 0 <= skipped <= total, "invalid L1D operand counts")
    return Fraction(skipped, total)


def coverage(decisions, groups, fractions):
    outcomes = {m: Counter() for m in clocks["CANDIDATES"]}
    for ref, *others in groups.values():
        for other in others:
            withhold = max(fractions[ref] + fractions[other]) >= Fraction(1, 100)
            for d in decisions[other].values():
                for m, count in outcomes.items():
                    if withhold:
                        count["abstain"] += 1
                    elif d["native"] == "indeterminate":
                        count["issued_native_mixed"] += 1
                    elif d[m] == "indeterminate":
                        count["model_mixed"] += 1
                    else:
                        count["issued_native_decisive"] += 1
                        count[previous["gshare"]["outcome"](d["native"], d[m])] += 1
    return outcomes


def classify(panel, plan):
    result = clocks["classify"](panel, plan)
    fractions = {n: [skipped_fraction(row["counters"]) for arm in ("flat", "outer") for row in arms[arm]]
                 for n, arms in panel.items()}
    result["skipped_operand_fraction"] = {n: [round6(min(v)), round6(max(v))] for n, v in fractions.items()}
    result["skip_guard"] = coverage(result["decisions"], plan["protocol"]["groups"], fractions)
    weights = {k: Fraction(str(v)) * 10**9 for k, v in plan["ml4"].items()}
    ratios = {}
    for order, (_, heap, ordered_set) in plan["protocol"]["groups"].items():
        medians = {}
        for n in (heap, ordered_set):
            scores, _ = clocks["measured"](panel[n], weights, Fraction(str(plan["gshare_miss_ns"])))
            scores["native"] = [r["cpu_us"] for r in panel[n]["native"]]
            medians[n] = {m: Fraction(median(values)) for m, values in scores.items()}
        ratios[order] = {m: x / medians[heap][m] for m, x in medians[ordered_set].items()}
    result["set_over_getchar_heap"] = {
        m: dict(once=round6(ratios["once"][m]), many=round6(ratios["many"][m]),
                many_over_once=round6(ratios["many"][m] / ratios["once"][m]),
                decreases=ratios["many"][m] < ratios["once"][m],
                relative_error={o: round6(r[m] / r["native"] - 1) for o, r in ratios.items()})
        for m in ratios["once"]}
    return result


def replay():
    plan, reg = registered()
    evidence = HERE / "evidence"
    cloud = read(evidence / "cloud.json")
    older = read(BASE / "evidence/cloud.json")
    for field in ("instance_id", "boot_disk_id", "boot_sha256"):
        current = {c[field] for c in cloud.values()}
        require(len(current) == 2 and current.isdisjoint(c[field] for c in older.values()), "reused guest identity")
    panels, result = {}, {}
    clients = {arm: plan["clients"][file] for arm, file in plan["protocol"]["clients"].items()}
    for phase in ("primary", "repeat"):
        arms, steal = previous["read_boot"](phase, cloud[phase], reg, plan, evidence=evidence, freeze=FREEZE,
            client_by_arm=clients, diagnostic_by_arm={"outer": "outer", "flat": "outer", "branch": "latency"})
        previous["read_branch"](evidence / (phase + ".tar.gz"), arms)
        panels[phase] = arms
        result[phase] = dict(steal_ticks=steal, **classify(arms, plan))
    pooled = {n: {a: rows + panels["repeat"][n][a] for a, rows in arms.items()}
              for n, arms in panels["primary"].items()}
    result["pooled"] = classify(pooled, plan)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
    result = replay()
    if args.verify:
        require(result == read(args.verify), "different published paired graph results")
        print("Verified 156 pinned executions, both graph oracles and four frozen relative clocks.")
    else:
        print(json.dumps(result, indent=2))
