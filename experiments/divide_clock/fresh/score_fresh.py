"""Evaluate fresh pinned-GCE runs with the already published assembly-only fit."""
import argparse
from fractions import Fraction
import gzip
import json
from pathlib import Path
import sys
from statistics import median

from study import rows
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from score import PLAN, PARAMETERS, coefficients, prepare, summarize
from corpus import require, sha

DATA = HERE.parents[1] / "results/divide-clock/fresh"
MODES = ("baseline", "sampled", "fast")


def cost(times):
    def ratios(numerator, denominator):
        return {"pooled_ppm": round(Fraction(sum(t[numerator] for t in times),sum(t[denominator] for t in times))*1_000_000),
                "median_pair_ppm": round(median(Fraction(t[numerator],t[denominator]) for t in times)*1_000_000)}
    return {"n": len(times), "total_cpu_ns": {k: sum(t[k] for t in times) for k in MODES},
            "sampled_over_baseline": ratios("sampled","baseline"), "fast_over_baseline": ratios("fast","baseline"),
            "fast_over_sampled": ratios("fast","sampled")}


def evaluate(data):
    plan, frozen = (json.loads(file.read_bytes()) for file in (PLAN,PARAMETERS))
    require(frozen["protocol_sha256"] == sha(PLAN.read_bytes()) and frozen["training"] == plan["train"],
            "fitting protocol differs")
    weights = coefficients(plan)
    groups = {k: plan[k] for k in ("fresh_anchors","fresh_holdouts")}
    cases = {n: {"native_ns": [], "runs": [], "time": [], "fast": [], "baseline": [], "div32": []}
             for members in groups.values() for n in members}
    for row, fixture, _ in rows(data):
        c = cases[fixture["workload_id"]]
        c["native_ns"].extend(row[k]["cpu_time_ns"] for k in ("native_before","native_after"))
        c["runs"].append({"production": row["sampled"]["production"], "feature": row["sampled"]["sample"]})
        c["time"].append({k: row[k]["cpu_time_ns"] for k in MODES})
        for k,version in (("fast",9001),("baseline",1)):
            obs = row[k]["production"]
            require(obs["latency_bound"]["version"] == version, "wrong clock version for treatment")
            cycles = obs["latency_bound"]["cycles_x16"]
            value = weights[0]*cycles/16 + weights[1]*obs["memory_operand_bytes"]["bytes"]
            c[k].append((value,cycles))
        blocks = row["sampled"]["sample"]["blocks"]
        c["div32"].append(sum(b["entries"]*sum(op["opcode"]=="div" and op["div_bits"]==32 for op in b["ops"])
                              for b in blocks))
    prepared = {name: prepare(c) for name,c in cases.items()}
    scores = summarize(prepared,groups,frozen["models"],plan,weights)
    tol = Fraction(plan["tolerance_ppm"],1_000_000)
    for name,c in cases.items():
        report = scores["cases"][name]
        target = prepared[name]["target"]
        for mode in ("baseline","fast"):
            errors = [v/target-1 for v,_ in c[mode]]
            report[mode] = {"estimates_ns": [round(v) for v,_ in c[mode]], "cycles_x16": [cyc for _,cyc in c[mode]],
                            "signed_errors_ppm": [round(e*1_000_000) for e in errors],
                            "pass": max(map(abs,errors)) <= tol}
        expected = report["models"]["latency_interval"]["cycles_x16"]
        report["fast_minus_replay_cycles_ppm"] = [round(Fraction(cyc-original,original)*1_000_000)
                                                  for (_,cyc),original in zip(c["fast"],expected,strict=True)]
        report["executed_main_div32"] = c["div32"]
        report["instrumented_cpu"] = cost(c["time"])
    pooled = {group: cost([t for n in members for t in cases[n]["time"]]) for group,members in groups.items()}
    pooled["all"] = cost([t for c in cases.values() for t in c["time"]])
    return {"parameters_sha256": sha(PARAMETERS.read_bytes()), "fresh_protocol_sha256": data["protocol_sha256"],
            "instrumented_cpu": pooled, **scores,
            "fast_meter_passes": {g: sum(scores["cases"][n]["fast"]["pass"] for n in names)
                                  for g,names in groups.items()}}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    raw = gzip.decompress((DATA/"dataset.json.gz").read_bytes())
    result = {"dataset_sha256": sha(raw), **evaluate(json.loads(raw))}
    destination = DATA/"evaluation.json"
    if args.verify:
        require(json.loads(destination.read_bytes()) == result,"fresh evaluation differs")
    else:
        destination.write_text(json.dumps(result,indent=2)+"\n")
    print(json.dumps(result["fast_meter_passes"]))
