"""Explain the 2x set-Dijkstra disagreement using matched solver and input identities."""
from collections import Counter, defaultdict
from fractions import Fraction
import gzip
import importlib.util
import json
from pathlib import Path
from statistics import median
import subprocess
import sys

from operands import HERE, MILAN, digest, read, schedule

spec = importlib.util.spec_from_file_location("verified_milan_panel", MILAN/"replay.py")
milan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(milan)
require = milan.require


def envelope(numerator, denominator):
    require(min(denominator) > 0, "relative measurements need a positive denominator")
    return [min(numerator)/max(denominator), max(numerator)/min(denominator)]


def ceiling(anchor, sample):
    """A nonnegative linear mixture cannot exceed its largest constituent ratio."""
    require(set(anchor) == set(sample) and anchor, "different constituent sets")
    bounds = {}
    for key in anchor:
        low, high = min(anchor[key]), max(sample[key])
        require(low >= 0 and min(sample[key]) >= 0, "nonnegative counters required")
        if low == 0:
            if high:
                return None, key
        else:
            bounds[key] = Fraction(high,low)
    require(bounds, "no positive denominator")
    key = max(bounds, key=bounds.get)
    return bounds[key], key


def operand_rows(plan, reg):
    doc = json.loads(gzip.decompress((HERE/"local-operands.json.gz").read_bytes()))
    names = plan["protocol"]["groups"]["dijkstra"]
    identities = {"probe.so","plan.json","dynamorio/bin64/drrun","dynamorio/lib64/release/libdynamorio.so"}
    identities |= {"bin/"+name for name in names} | {plan["cases"][name]["stdin"] for name in names}
    require(doc["schema"] == 1 and doc["files"] == {k:reg["payload"][k] for k in identities}, "local input identity")
    rows = doc["trials"]
    counts = defaultdict(list)
    cpu = defaultdict(lambda:defaultdict(list))
    shared = defaultdict(list)
    native_report = milan.collector
    for row, (rep,name,mode) in zip(rows, schedule(names), strict=True):
        require((row["rep"],row["name"],row["mode"]) == (rep,name,mode) and row["exit_code"] == 0 and row["cpu_us"] > 0
                and digest(row["stdout"].encode()) == plan["cases"][name]["expected_sha256"], "different local run")
        pid = row["pid"]
        report = native_report["aggregate"]({pid:native_report["parse_records"](pid,row["records"].encode())},pid)
        require(report["complete"] and report["valid"] and report["quality"] == "uninterrupted"
                and report["process_count"] == report["image_count"] == 1 and report["cpuid_profile"] == "x86-64-v2"
                and report["processes"][0]["exit_code"] == 0 and "latency_bound" in report
                and (mode == "operand") == ("memory_operand_bytes" in report), "different counter mode")
        shared[name].append((report["instructions"],report["latency_bound"]["cycles_x16"],report["rep_bytes"]))
        if mode == "operand":
            counts[name].append(report["memory_operand_bytes"]["bytes"])
        cpu[name][mode].append(row["cpu_us"])
    require(all(len(set(v)) == 1 for v in shared.values()), "base/operand common counters differ")
    return counts, {n:v[0] for n,v in shared.items()}, {n:median(v["operand"])/median(v["base"]) for n,v in cpu.items()}


def constraint(anchor_score, sample_score, anchor_skipped, sample_skipped, factor):
    difference = sample_skipped - factor*anchor_skipped
    require(difference > 0, "witness needs a positive skipped-count difference")
    return (factor*anchor_score - sample_score)/difference


def old_residual_fit(cases, excluded, weights):
    """One new nonnegative coefficient, holding all original ML4 weights fixed."""
    terms = []
    for name, case in cases.items():
        if case["role"] == "calibration" and name != excluded:
            p = case["probe"][0]
            y = Fraction(str(median(case["native_cpu_s"])))
            x = p["l1d_skipped"] / y
            predicted = milan.predict(weights,milan.features(p))
            terms.append((x, 1-predicted/y))
    require(len(terms) == 35, "changed historical training set")
    return max(Fraction(0),sum(x*err for x,err in terms)/sum(x*x for x,_ in terms))


def exploratory_verdicts(samples, groups, factors, score, skipped, coefficients):
    results = {}
    for phase, cases in samples.items():
        stats = Counter()
        errors = []
        for problem,(anchor,*targets) in groups.items():
            def ratio_score(name):
                return score(name,problem) + coefficients[problem]*skipped(name)
            for name in targets:
                for factor in factors:
                    native = milan.relative_verdict(cases[name]["native"],cases[anchor]["native"],factor)
                    forecast = "accept" if ratio_score(name) <= Fraction(str(factor))*ratio_score(anchor) else "timeout"
                    outcome = ("indeterminate" if native == "indeterminate" else "agree" if native == forecast
                               else "false_accept" if forecast == "accept" else "false_timeout")
                    stats[outcome] += 1
                    if outcome.startswith("false"):
                        errors.append(dict(name=name, factor=factor, outcome=outcome))
        results[phase] = dict(outcomes=dict(stats), errors=errors)
    return results


def main():
    reg, plan = read(MILAN/"registration.json"), read(MILAN/"frozen.json")
    require(milan.replay() == read(MILAN/"results.json"), "Milan archive replay differs")
    reg["payload"] = {r["path"]:r["sha256"] for r in reg["files"]}
    cloud = read(MILAN/"evidence/cloud.json")
    samples = {ph:milan.read_boot(MILAN/"evidence"/(ph+".tar.gz"),ph,plan,reg,cloud[ph])[0] for ph in ("primary","repeat")}
    intended, heap, tree = plan["protocol"]["groups"]["dijkstra"]
    names = [intended, heap, tree]
    probes = {name:sum((phase[name]["probe"] for phase in samples.values()),[]) for name in plan["cases"]}
    require(all(milan.probe_repeatable(v) for v in probes.values()), "full counters changed between runs")
    operands, local_shared, overhead = operand_rows(plan, reg)
    deltas = {}
    for name in names:
        counter = probes[name][0]
        guest = (counter["instructions"],counter["latency_bound_x16"],counter["movs_bytes"]+counter["stos_bytes"])
        local = local_shared[name]
        require(local[2] == guest[2], "different local REP path")
        deltas[name] = max(1_000_000*abs(a-b)/b for a,b in zip(local,guest,strict=True))
    weights = {g:{k:Fraction(str(v)) for k,v in plans["ML4"].items()} for g,plans in plan["weights"].items()}

    def score(name, problem):
        return milan.predict(weights[problem],milan.features(probes[name][0]))

    def skipped(name):
        return probes[name][0]["l1d"][3]

    columns = {n:defaultdict(list) for n in names}
    for n in names:
        for p in probes[n]:
            for feature, value in {**milan.features(p), "l1d_refs":p["l1d"][0], "l1d_lines":p["l1d"][1]}.items():
                columns[n][feature].append(value)
        columns[n]["memory_operand_bytes"] = operands[n]
        columns[n]["constant"] = [1]
    cap, term = ceiling(columns[intended], columns[tree])
    anchor_score = score(intended,"dijkstra")
    breakdown = {}
    for feature in sorted(columns[intended]):
        a = columns[intended][feature]
        s = columns[tree][feature]
        w = weights["dijkstra"].get(feature,0)
        breakdown[feature] = dict(set_over_intended=envelope(s,a), set_over_matched_heap=envelope(s,columns[heap][feature]),
                                  intended_score_share=float(w*a[0]/anchor_score),
                                  set_score_in_anchor_units=float(w*s[0]/anchor_score))
    gaps = {}
    for ph, data in samples.items():
        gaps[ph] = {}
        for label, n, a in (("heap_over_intended",heap,intended),("set_over_heap",tree,heap),("set_over_intended",tree,intended)):
            gaps[ph][label] = dict(native_all_pairs=envelope(data[n]["native"],data[a]["native"]),
                                   native_medians=median(data[n]["native"])/median(data[a]["native"]),
                                   ml4=float(score(n,"dijkstra")/score(a,"dijkstra")))
    # Inspect the missing-operand counter as a possible extra charge, keeping every published ML4 weight unchanged.
    bounds = {}
    for problem,n,expected,side in (("dijkstra",tree,"timeout","lower_exclusive"),
                                     ("bfs_grid","bfs_grid--list_queue","accept","upper_inclusive")):
        anchor = plan["protocol"]["groups"][problem][0]
        native = samples["primary"]
        require(milan.relative_verdict(native[n]["native"],native[anchor]["native"],2) == expected,
                "unexpected 2x native outcome")
        coefficient = constraint(score(anchor,problem),score(n,problem),skipped(anchor),skipped(n),2)
        bounds[side] = dict(problem=problem, anchor=anchor, variant=n, anchor_skipped=skipped(anchor), variant_skipped=skipped(n),
                            seconds_per_skipped=[coefficient.numerator,coefficient.denominator],
                            ns_per_skipped=float(coefficient*1_000_000_000))
    # The earlier Xeon calibration omits these variants and the Dijkstra anchor. Its probe predates the correction.
    prior = read(MILAN.parent/"results/v2/results.json")
    previous = prior["cases"]
    for n in names:
        require(previous[n]["stdin_sha256"] == plan["cases"][n]["stdin_sha256"]
                and previous[n]["expected_sha256"] == plan["cases"][n]["expected_sha256"]
                and prior["built"][n]["source_sha256"] == plan["cases"][n]["source_sha256"], "different Xeon participant")
    coefficients = {g:old_residual_fit(previous, group[0],weights[g]) for g,group in plan["protocol"]["groups"].items()}
    pooled = {n:{"native":samples["primary"][n]["native"] + samples["repeat"][n]["native"]} for n in probes}
    augmented = exploratory_verdicts({**samples,"pooled":pooled},plan["protocol"]["groups"],plan["protocol"]["factors"],
                                     score,skipped,coefficients)
    old_range = envelope(previous[tree]["native_cpu_s"],previous[intended]["native_cpu_s"])
    # Use the separate study's own reference, input, ELF and original absolute-time protocol.
    cross = MILAN.parent/"aonohako_shortest_paths"
    independent = json.loads(subprocess.check_output([sys.executable,"-B",str(cross/"replay.py")]))
    require(independent == read(cross/"results.json"), "independent graph replay differs")
    panel = independent["pooled"]["cases"]
    independent_range = envelope(panel["ordered_set"]["native_ns"],panel["reference"]["native_ns"])
    return dict(schema=1, same_input=plan["cases"][tree]["stdin_sha256"], cases=names, per_boot=gaps,
                all_feature_linear_ceiling={"term":term, "numerator":cap.numerator, "denominator":cap.denominator, "ratio":float(cap)},
                breakdown=breakdown, skipped_charge=bounds,
                historical_residual_fit=dict(ns_per_skip={g:float(c*1_000_000_000) for g,c in coefficients.items()},
                                             transfer=augmented), operand_observations=dict(operands),
                shared_counter_local_gce_max_delta_ppm=deltas, local_operand_over_base_cpu_median_ratio=overhead, xeon_native_ratio=old_range,
                independent_graph={"native_set_over_own_heap":independent_range,
                                   "set_verdicts_at_65ms":panel["ordered_set"]["verdicts"]})


if __name__ == "__main__":
    print(json.dumps(main(),indent=2,sort_keys=True))
