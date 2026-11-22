#!/usr/bin/env python3
"""Score the frozen address-span, interpolation, and paired instrumentation-cost tests."""
import argparse
from decimal import Decimal, localcontext
import gzip
import json
from pathlib import Path
import runpy
from statistics import median
import tarfile

HERE = Path(__file__).resolve().parent
sampler = runpy.run_path(str(HERE.parent / "access_sample/study.py"))
require, sha, uint = (sampler[key] for key in ("require", "sha", "uint"))
ARMS = ("native", "diag", "diag_near", "diag_span", "live", "live_span")
REPS = range(1, 7)
SPAN = "sampled_address_span_bytes"
D = Decimal


def ppm(ratio):
    return round(ratio * 1_000_000)


def distribution(values):
    values = list(values)
    return {"min": min(values), "median": median(values), "max": max(values)}


def hot_site(sample, arm, pid, case):
    """Validate the v1 contract on shared fields, plus every v2 span."""
    with_span = arm in ("diag_span", "live_span")
    require(uint(sample["schema"]) and sample["schema"] == (2 if with_span else 1), "wrong sample schema")
    if with_span:
        for site in sample["sites"]:
            require(uint(site[SPAN]) and (site["loads"] != 1 or site[SPAN] == 0), "invalid site span")
        common_sites = [{k: v for k, v in site.items() if k != SPAN} for site in sample["sites"]]
        common = {**sample, "schema": 1, "sites": common_sites}
    else:
        common = sample
    sampler["validate_sample"](common, pid)
    if "oracle" in case:
        return next((s for s in sample["sites"] if s["offset"] == case["oracle"]["offset"]), None)
    return None


def scored_rows(data, archive, cases):
    indexed = {}
    required = {}
    warmups = set()
    for row in data["rows"]:
        name, arm, rep = row["case_id"], row["arm"], row["rep"]
        case = cases[name]
        require(row["exit_code"] == 0 and row["stdout_sha256"] == case["stdout_sha256"], f"bad output: {name}")
        require(all(uint(row[key]) for key in ("rep", "cpu_us", "user_us", "sys_us"))
                and row["cpu_us"] == row["user_us"] + row["sys_us"] > 0, "bad CPU accounting")
        if arm == "warmup":
            require(rep == 0 and name not in warmups, "duplicate or misplaced warmup")
            warmups.add(name)
            continue
        key = name, rep, arm
        require(arm in ARMS and rep in REPS and key not in indexed, f"unexpected or duplicate run: {key}")
        obs = indexed[key] = {"cpu_ns": row["cpu_us"] * 1000}
        kinds = []
        if arm in ("diag", "diag_span", "live", "live_span"):
            kinds.append("production")
        if arm in ("diag_near", "diag_span", "live_span"):
            kinds.append("sample")
        for kind in kinds:
            path = f"runs/{row['sequence']:03d}/{kind}.json"
            require(path in row["receipts"] and path not in required, f"missing or duplicate receipt: {path}")
            required[path] = row["receipts"][path], kind, row["launcher_pid"], case, arm, obs
    require(len(indexed) == len(cases) * len(REPS) * len(ARMS) and warmups == cases.keys(),
            "missing runs or warmups")
    seen = set()
    with tarfile.open(archive, mode="r|*") as receipts:
        for member in receipts:
            require(member.name not in seen, "duplicate archive member")
            seen.add(member.name)
            if member.name not in required:
                continue
            digest, kind, pid, case, arm, obs = required.pop(member.name)
            require(member.isfile() and 0 < member.size <= 4 * 1024 * 1024, "invalid receipt member")
            raw = receipts.extractfile(member).read()
            require(sha(raw) == digest, f"wrong receipt digest: {member.name}")
            receipt = sampler["read_sample"](raw)
            if kind == "sample":
                obs["site"] = hot_site(receipt, arm, pid, case)
            else:
                require(receipt["root_pid"] == pid and uint(receipt["instructions"], 1)
                        and uint(receipt["rep_bytes"]), "invalid production work")
                obs["work"] = receipt["instructions"] + receipt["rep_bytes"]
                if arm == "diag":
                    cycles = receipt["latency_bound"]
                    require(cycles["version"] == 1 and uint(cycles["cycles_x16"], 1), "invalid diagnostic latency")
                    obs["cycles_x16"] = cycles["cycles_x16"]
    require(not required, "missing archived receipts")
    return indexed


def features(cases, rows):
    details = {}
    spans = {}
    for name, case in cases.items():
        if "oracle" not in case:
            continue
        oracle = case["oracle"]
        observed = []
        diag = []
        passed = True
        near_counts, far_counts, loads, pairs = [], [], [], []
        for rep in REPS:
            for arm in ("diag_near", "diag_span", "live_span"):
                site = rows[name, rep, arm]["site"]
                if site is None:
                    passed = False
                    continue
                loads.append(site["loads"])
                pairs.append(site["pairs"])
                near_counts.append(site["near_pairs"])
                far_counts.append(site["far_pairs"])
                passed &= (site["loads"] == oracle["visits"] and site["pairs"] == oracle["pairs"] and
                           site["near_pairs"] in oracle["near_pairs_by_base_mod64_div4"])
                if arm != "diag_near":
                    observed.append(site[SPAN])
                    passed &= site[SPAN] == oracle["sampled_span_bytes"]
                    if arm == "diag_span":
                        diag.append(site[SPAN])
        active = case["active_bytes"]
        covered = len(observed) == 2 * len(REPS) and all(9 * active <= 10 * x <= 10 * active for x in observed)
        stable_diag = len(diag) == len(REPS) and len(set(diag)) == 1
        spans[name] = diag[0] if stable_diag else None
        details[name] = {"active_bytes": active, "expected_span_bytes": oracle["sampled_span_bytes"],
                         "span_bytes": sorted(set(observed)), "hot_site_reports": len(loads),
                         "coverage_ppm": sorted({ppm(D(n) / active) for n in observed}),
                         "loads": sorted(set(loads)), "pairs": sorted(set(pairs)),
                         "near_pairs": sorted(set(near_counts)), "far_pairs": sorted(set(far_counts)),
                         "far_share_ppm": sorted({ppm(D(f) / p) for f, p in zip(far_counts, pairs) if p}),
                         "oracle_pass": passed, "coverage_pass": covered}
    ordered = [detail["span_bytes"] for detail in sorted(details.values(), key=lambda v: v["active_bytes"])]
    monotone = all(ordered) and all(max(a) < min(b) for a, b in zip(ordered, ordered[1:]))
    return {"cases": details, "strict_order_pass": monotone,
            "pass": monotone and all(v["oracle_pass"] and v["coverage_pass"] for v in details.values())}, spans


def estimate_result(values, native):
    if not values:
        return {"pass": False, "reason": "span unavailable, unstable, or outside calibration interval"}
    center = median(native)
    relative = [v / center - 1 for v in values]
    minimum_errors = [v / min(native) - 1 for v in values]
    return {"estimates_ns": [round(v) for v in values],
            "error_ppm": [ppm(v) for v in relative],
            "relative_to_native_minimum_ppm": [ppm(v) for v in minimum_errors],
            "pass": all(abs(v) <= D("0.2") for v in relative)}


def timing(cases, rows, spans, plan):
    anchors = plan["timing_test"]["calibration"]
    fresh = plan["timing_test"]["fresh"]
    require(len(anchors) == 2 and len(fresh) == 3 and set(anchors).isdisjoint(fresh)
            and set(anchors + fresh) == spans.keys(), "split differs from the walk cases")
    native = {name: [D(rows[name, rep, "native"]["cpu_ns"]) for rep in REPS] for name in cases}
    cycles = {name: [D(rows[name, rep, "diag"]["cycles_x16"]) / 16 for rep in REPS] for name in spans}
    ratios = [median(cycles[name]) / median(native[name]) for name in anchors]
    coefficient = sum(ratios) / sum(v * v for v in ratios)
    low, high = (spans[name] for name in anchors)
    tlo, thi = (median(native[name]) for name in anchors)
    heldout = {}
    for name in fresh:
        span = spans[name]
        predictions = []
        if span and low and high and 0 < low < high and low <= span <= high:
            # The logarithm base cancels in the interpolation weight.
            weight = (D(span) / low).ln() / (D(high) / low).ln()
            predictions = [tlo + (thi - tlo) * weight]
        heldout[name] = {"span": estimate_result(predictions, native[name]),
                         "latency_only": estimate_result([coefficient * v for v in cycles[name]], native[name])}
    native_summary = {}
    for name, values in native.items():
        spread = (max(values) - min(values)) / median(values)
        native_summary[name] = {"range_ns": {k: int(v) for k, v in distribution(values).items()},
                                "spread_ppm": ppm(spread), "spread_pass": spread <= D("0.10"),
                                "duration_pass": min(values) >= 20_000_000}
    return {"native": native_summary,
            "calibration": {name: {"median_native_ns": int(median(native[name])), "span_bytes": spans[name],
                                   "median_cycles_x16": str(median(cycles[name]) * 16)} for name in anchors},
            "latency_only_ns_per_cycle": str(coefficient), "fresh": heldout,
            "cases_within_20_percent": {model: sum(v[model]["pass"] for v in heldout.values())
                                        for model in ("span", "latency_only")}}


def paired_cost(pairs):
    base, instrumented = (sum(v[i] for v in pairs) for i in (0, 1))
    ratios = [D(on) / off for off, on in pairs]
    return {"pairs": len(pairs), "base_cpu_ns": base, "on_cpu_ns": instrumented,
            "pooled_ratio_ppm": ppm(D(instrumented) / base),
            "paired_ratio_ppm": {k: ppm(v) for k, v in distribution(ratios).items()}}


def costs(cases, rows):
    results = {}
    for base, on in (("diag", "diag_span"), ("live", "live_span"),
                     ("diag", "diag_near"), ("diag_near", "diag_span")):
        per_case = {name: [(rows[name, rep, base]["cpu_ns"], rows[name, rep, on]["cpu_ns"]) for rep in REPS]
                    for name in cases}
        summaries = {name: paired_cost(pairs) for name, pairs in per_case.items()}
        groups = {"all": list(cases), "walks": [n for n, case in cases.items() if "oracle" in case],
                  "controls": [n for n, case in cases.items() if "oracle" not in case]}
        pooled = {k: paired_cost([p for name in names for p in per_case[name]]) for k, names in groups.items()}
        result = {"base_arm": base, "on_arm": on, "by_case": summaries, "pooled": pooled}
        if (base, on) in (("diag", "diag_span"), ("live", "live_span")):
            all_cases = pooled["all"]
            group_pass = 100 * all_cases["on_cpu_ns"] <= 105 * all_cases["base_cpu_ns"]
            case_pass = {n: 10 * v["on_cpu_ns"] <= 11 * v["base_cpu_ns"] for n, v in summaries.items()}
            result.update(pooled_pass=group_pass, case_pass=case_pass, passed=group_pass and all(case_pass.values()))
        results[f"{on}_vs_{base}"] = result
    return results


def work_differences(cases, rows):
    result = {}
    for arm in ("diag", "live"):
        result[arm] = {}
        for name in cases:
            pairs = [(rows[name, rep, arm]["work"], rows[name, rep, arm + "_span"]["work"]) for rep in REPS]
            differences = [on - off for off, on in pairs]
            result[arm][name] = {"min_units": min(differences), "max_units": max(differences),
                                 "equal_pairs": differences.count(0),
                                 "max_abs_ppm": max(ppm(abs(D(on - off) / off)) for off, on in pairs)}
    return result


def evaluate(dataset, receipts):
    raw = dataset.read_bytes()
    raw = gzip.decompress(raw) if raw.startswith(b"\x1f\x8b") else raw
    data = json.loads(raw)
    cases_raw = (HERE / "cases.json").read_bytes()
    plan_raw = (HERE / "analysis-plan.json").read_bytes()
    protocol_raw = (HERE / "protocol.json").read_bytes()
    require(sha(protocol_raw) == data["protocol_sha256"], "different protocol")
    protocol = json.loads(protocol_raw)
    require(sha(cases_raw) == protocol["tools_sha256"]["cases.json"], "different input manifest")
    require(len(data["rows"]) == len(protocol["schedule"]) and
            all(row["sequence"] == i and all(row[k] == v for k, v in expected.items())
                for i, (row, expected) in enumerate(zip(data["rows"], protocol["schedule"]))), "unexpected schedule")
    cases = {c["case_id"]: c for c in json.loads(cases_raw)}
    rows = scored_rows(data, receipts, cases)
    with localcontext() as context:
        context.prec = 50
        feature, spans = features(cases, rows)
        return {"schema": 1, "dataset_sha256": sha(raw), "receipts_sha256": sha(receipts.read_bytes()),
                "cases_sha256": sha(cases_raw), "analysis_plan_sha256": sha(plan_raw),
                "protocol_sha256": data["protocol_sha256"], "feature": feature,
                "timing": timing(cases, rows, spans, json.loads(plan_raw)), "cost": costs(cases, rows),
                "descriptive_paired_work_differences": work_differences(cases, rows)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("receipts", type=Path)
    out = parser.add_mutually_exclusive_group()
    out.add_argument("--output", type=Path)
    out.add_argument("--verify", type=Path)
    args = parser.parse_args()
    result = evaluate(args.dataset, args.receipts)
    if args.verify:
        require(result == json.loads(args.verify.read_bytes()), "saved evaluation differs")
        print("Verified the sampled spans, three held-out timing estimates and paired CPU cost.")
    elif args.output:
        args.output.write_text(json.dumps(result, indent=2) + "\n")
    else:
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
