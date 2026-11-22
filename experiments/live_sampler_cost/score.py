#!/usr/bin/env python3
"""Replay paired live-sampler costs and compare the three per-site counter signatures."""
import argparse
from fractions import Fraction
import gzip
import json
from pathlib import Path
import runpy
from statistics import median
import struct
import tarfile

HERE = Path(__file__).resolve().parent
PREVIOUS = HERE.parent / "cpp20_locality"
locality = runpy.run_path(str(PREVIOUS / "score.py"))
sha, uint, require = (locality[k] for k in ("sha", "uint", "require"))
read_json = locality["sampler"]["read_sample"]
REPS = range(1, 7)
LIVE_ARMS = ("live", "empty", "gate", "full", "fused")
WORK = ("instructions", "movs_bytes", "stos_bytes")
SPAN = "sampled_address_span_bytes"


def read_sidecar(raw, arm, pid, case):
    report = read_json(raw)
    if arm in ("full", "fused"):
        # Both complete implementations use the #78 live-span ABI.
        locality["hot_site"](report, "live_span", pid, case)
        return {s["offset"]: s for s in report["sites"]}
    require(report["arm"] == arm and uint(report["schema"]) and report["schema"] == 1
            and uint(report["pid"], 1) and report["pid"] == pid and uint(report["threads"])
            and report["threads"] == 1, "wrong control identity")
    fields = {"schema", "arm", "pid", "threads"}
    if arm == "empty":
        require(set(report) == fields, "unexpected empty-control fields")
        return {}
    require(arm == "gate" and set(report) == fields | {"period", "loads", "pairs", "sites"}
            and uint(report["period"]) and report["period"] == 1024 and isinstance(report["sites"], list),
            "unexpected gate-control fields")
    sites = {}
    loads = pairs = 0
    previous = -1
    for site in report["sites"]:
        require(set(site) == {"offset", "loads", "pairs"} and all(uint(v) for v in site.values())
                and site["offset"] > previous and site["loads"] > 0, "invalid or unordered gate site")
        previous = site["offset"]
        full, remainder = divmod(site["loads"], 1024)
        require(site["pairs"] == full + (remainder >= 2), "invalid gate cadence")
        sites[site["offset"]] = site
        loads += site["loads"]
        pairs += site["pairs"]
    require(uint(report["loads"]) and uint(report["pairs"]) and (loads, pairs) == (report["loads"], report["pairs"]),
            "invalid gate totals")
    return sites


def observations(path, protocol, cases, dataset):
    schedule = protocol["schedule"]
    rows = dataset["rows"]
    require(len(schedule) == len(rows) == len(cases) * (len(REPS) * (1 + len(LIVE_ARMS)) + 1), "wrong trial count")
    with tarfile.open(path, "r|*") as archive:
        receipts = {}
        for member in archive:
            require(member.name not in receipts and member.isfile(), "duplicate or unexpected receipt")
            receipts[member.name] = archive.extractfile(member).read()
    results = {}
    for i, (row, slot) in enumerate(zip(rows, schedule)):
        name, arm, rep = (slot[k] for k in ("case_id", "arm", "rep"))
        case = cases[name]
        require(row["sequence"] == i and all(row[k] == v for k, v in slot.items())
                and row["exit_code"] == 0 and row["stdout_sha256"] == case["stdout_sha256"], "trial identity mismatch")
        require(all(uint(row[k]) for k in ("cpu_us", "user_us", "sys_us"))
                and row["cpu_us"] == row["user_us"] + row["sys_us"] > 0, "invalid wait4 time")
        pid = row["launcher_pid"]
        files = {}
        prefix = f"runs/{i:03d}/"
        for member, digest in row["receipts"].items():
            require(member.startswith(prefix), "wrong receipt sequence")
            raw = receipts.pop(member)
            require(sha(raw) == digest, f"changed receipt {member}")
            files[member[len(prefix):]] = raw
        require(files["stdout"] == case["stdout"].encode() and files["stderr"] == b"", "unexpected trial output")
        if arm == "warmup":
            require(rep == 0, "invalid warmup")
            continue
        key = name, rep, arm
        require(arm in ("native", *LIVE_ARMS) and rep in REPS and key not in results, "unexpected or duplicate run")
        obs = results[key] = {"cpu_ns": row["cpu_us"] * 1000}
        if arm == "native":
            continue
        report = read_json(files["production.json"])
        breakdown = report["rep_breakdown"]
        require(report["root_pid"] == pid and report["valid"] is True and report["complete"] is True and
                report["authority"] == "diagnostic_only" and report["integrity_verified"] is False and
                report["quality"] == "uninterrupted" and report["signals_observed"] == 0 and
                report["cpuid_profile"] == "x86-64-v2" and "latency_bound" not in report and
                "memory_operand_bytes" not in report and breakdown["version"] == 1, "wrong live-mode receipt")
        work = (report["instructions"], breakdown["movs_bytes"], breakdown["stos_bytes"])
        require(all(uint(v) for v in work) and work[0] > 0 and report["rep_bytes"] == sum(work[1:]) and
                sum(work) <= protocol["work_budget"] and files["clock.bin"] == struct.pack("<6Q", 1, 0, *work, 0),
                "terminal clock differs from the counters or exceeds the budget")
        obs["work"] = dict(zip(WORK, work))
        if arm != "live":
            obs["sites"] = read_sidecar(files["sample.json"], arm, pid, case)
    require(not receipts and len(results) == len(cases) * len(REPS) * 6, "unassigned receipts or missing runs")
    return results


def ppm(value):
    return round(value * 1_000_000)


def bounds(values):
    return {"min": min(values), "median": median(values), "max": max(values)}


def paired_summary(pairs):
    base = sum(b for b, c in pairs)
    candidate = sum(c for b, c in pairs)
    delta = [c - b for b, c in pairs]
    ratios = [Fraction(c, b) for b, c in pairs]
    return {"pairs": len(pairs), "base_cpu_ns": base, "candidate_cpu_ns": candidate,
            "pooled_delta_ns": candidate - base, "pooled_ratio_ppm": ppm(Fraction(candidate, base)),
            "paired_delta_ns": bounds(delta),
            "paired_ratio_ppm": {k: ppm(v) for k, v in bounds(ratios).items()}}


def decision(all_cases, by_case, pooled, each):
    def passes(value, percent):
        return 100 * value["candidate_cpu_ns"] <= percent * value["base_cpu_ns"]
    per_case = {name: passes(value, each) for name, value in by_case.items()}
    good = passes(all_cases, pooled)
    return {"pooled_pass": good, "case_pass": per_case, "pass": good and all(per_case.values())}


def costs(cases, rows, comparisons):
    result = {}
    groups = {"walks": [name for name, case in cases.items() if "oracle" in case],
              "controls": [name for name, case in cases.items() if "oracle" not in case],
              "all": list(cases)}
    for base, candidate in comparisons:
        pairs = {name: [(rows[name, rep, base]["cpu_ns"], rows[name, rep, candidate]["cpu_ns"])
                        for rep in REPS] for name in cases}
        by_case = {name: paired_summary(values) for name, values in pairs.items()}
        pooled = {label: paired_summary([p for n in names for p in pairs[n]]) for label, names in groups.items()}
        item = {"base": base, "candidate": candidate, "by_case": by_case, "pooled": pooled}
        if candidate == "fused" and base == "live":
            item["overhead_gate"] = decision(pooled["all"], by_case, 105, 110)
        if candidate == "fused" and base == "full":
            item["optimization_gate"] = decision(pooled["all"], by_case, 97, 102)
        result[f"{candidate}_vs_{base}"] = item
    return result


def signature(a, b):
    def value(site):
        return [site["loads"], site["pairs"]] if site else None
    changes = []
    for offset in sorted(a.keys() | b.keys()):
        before, after = value(a.get(offset)), value(b.get(offset))
        if before != after:
            changes.append({"offset": offset, "base": before, "candidate": after})
    return changes


def compare_sites(cases, rows):
    failures = []
    detail = {}
    for name, case in cases.items():
        counts = {"gate_vs_full_equal": 0, "full_vs_fused_equal": 0, "hot_oracles_checked": 0}
        near_differences = span_differences = common_sites = 0
        for rep in REPS:
            sites = {arm: rows[name, rep, arm]["sites"] for arm in ("gate", "full", "fused")}
            for a, b in (("gate", "full"), ("full", "fused")):
                changes = signature(sites[a], sites[b])
                if changes:
                    failures.append({"case_id": name, "rep": rep, "base": a, "candidate": b, "sites": changes})
                else:
                    counts[f"{a}_vs_{b}_equal"] += 1
            oracle = case.get("oracle")
            if oracle:
                for arm in sites:
                    counts["hot_oracles_checked"] += 1
                    s = sites[arm].get(oracle["offset"])
                    good = s is not None and s["loads"] == oracle["visits"] and s["pairs"] == oracle["pairs"]
                    if good and arm != "gate":
                        good = (s["near_pairs"] in oracle["near_pairs_by_base_mod64_div4"] and
                                s["far_pairs"] == oracle["pairs"] - s["near_pairs"] and
                                s[SPAN] == oracle["sampled_span_bytes"])
                    if not good:
                        failures.append({"case_id": name, "rep": rep, "arm": arm, "hot_site": s})
            for offset in sites["full"].keys() & sites["fused"].keys():
                if oracle and offset == oracle["offset"]:
                    continue
                full, fused = sites["full"][offset], sites["fused"][offset]
                common_sites += 1
                near_differences += (full["near_pairs"], full["far_pairs"]) != (fused["near_pairs"], fused["far_pairs"])
                span_differences += full[SPAN] != fused[SPAN]
        counts["descriptive_non_oracle_site_comparisons"] = common_sites
        counts["descriptive_classification_differences"] = near_differences
        counts["descriptive_span_differences"] = span_differences
        detail[name] = counts
    return {"cases": detail, "failures": failures, "pass": not failures}


def work_differences(cases, rows):
    result = {}
    for name in cases:
        pairs = [(rows[name, rep, "full"]["work"], rows[name, rep, "fused"]["work"]) for rep in REPS]
        metrics = {}
        for key in WORK:
            differences = [b[key] - a[key] for a, b in pairs]
            metrics[key] = {"min": min(differences), "max": max(differences), "equal_pairs": differences.count(0)}
        result[name] = {"all_three_equal_pairs": sum(a == b for a, b in pairs), "signed_differences": metrics}
    return result


def evaluate(dataset, receipts):
    raw = dataset.read_bytes()
    if raw.startswith(b"\x1f\x8b"):
        raw = gzip.decompress(raw)
    data = json.loads(raw)
    protocol_raw = (HERE / "protocol.json").read_bytes()
    protocol = json.loads(protocol_raw)
    design_raw = (HERE / "design.json").read_bytes()
    design = json.loads(design_raw)
    require(sha(design_raw) == protocol["tools_sha256"]["design.json"], "different registered analysis")
    require(data["schema"] == 1 and data["protocol_sha256"] == sha(protocol_raw), "wrong collection protocol")
    cases_raw = (PREVIOUS / "cases.json").read_bytes()
    require(sha(cases_raw) == protocol["tools_sha256"]["cases.json"] == design["cases_sha256"], "different corpus")
    cases = {c["case_id"]: c for c in json.loads(cases_raw)}
    rows = observations(receipts, protocol, cases, data)
    native = {name: {k: int(n) for k, n in bounds([rows[name, r, "native"]["cpu_ns"] for r in REPS]).items()}
              for name in cases}
    return {"schema": 1, "protocol_sha256": sha(protocol_raw), "design_sha256": sha(design_raw),
            "dataset_sha256": sha(raw), "receipts_sha256": sha(receipts.read_bytes()), "native_ns": native,
            "integrity": compare_sites(cases, rows), "work_differences": work_differences(cases, rows),
            "cost": costs(cases, rows, design["estimation"]["comparisons"])}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("dataset", type=Path)
    p.add_argument("receipts", type=Path)
    group = p.add_mutually_exclusive_group()
    group.add_argument("--output", type=Path)
    group.add_argument("--verify", type=Path)
    args = p.parse_args()
    result = evaluate(args.dataset, args.receipts)
    if args.verify:
        require(json.loads(args.verify.read_bytes()) == result, "saved evaluation differs")
        print("Verified live overhead, fusion savings, clock and per-site signatures.")
    elif args.output:
        args.output.write_text(json.dumps(result, indent=2) + "\n")
    else:
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
