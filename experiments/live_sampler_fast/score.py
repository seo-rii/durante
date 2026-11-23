#!/usr/bin/env python3
"""Score packed and ELF-profiled sampling using the frozen inputs and counter oracles."""
import argparse
from collections import Counter
from fractions import Fraction
import gzip
import json
from pathlib import Path
import runpy
import struct
import tarfile

HERE = Path(__file__).resolve().parent
prior = runpy.run_path(str(HERE.parent / "live_sampler_cost/score.py"))
decode_packed = runpy.run_path(str(HERE / "probe/packed.py"))["decode_packed"]
require, uint, sha, ppm = (prior[k] for k in ("require", "uint", "sha", "ppm"))
REPS, WORK, SPAN = prior["REPS"], prior["WORK"], prior["SPAN"]
READ_JSON = prior["read_json"]


def read_sites(raw, arm, pid, case):
    if arm in ("gate", "full", "profiled"):
        return prior["read_sidecar"](raw, "full" if arm == "profiled" else arm, pid, case)
    if arm == "packed":
        report = decode_packed(raw)
        prior["locality"]["hot_site"](report, "live_span", pid, case)
        return {s["offset"]: s for s in report["sites"]}
    catalog = READ_JSON(raw)
    require(arm == "catalog" and set(catalog) == {"schema", "arm", "pid", "threads", "sites"}
            and catalog["arm"] == arm and all(uint(catalog[k]) and catalog[k] == v
                for k, v in (("schema", 1), ("pid", pid), ("threads", 1))), "wrong catalog header")
    sites = catalog["sites"]
    require(isinstance(sites, list) and all(uint(v) for v in sites) and sites == sorted(set(sites)),
            "catalog offsets must be ordered and unique")
    return set(sites)


def observations(dataset, path, protocol, cases, arms):
    with tarfile.open(path, "r|*") as archive:
        contents = {}
        for m in archive:
            require(m.isfile() and m.name not in contents, "invalid receipt archive")
            contents[m.name] = archive.extractfile(m).read()
    output = {}
    require(len(dataset["rows"]) == len(protocol["schedule"]) == len(cases) * (len(arms) * len(REPS) + 1),
            "wrong number of runs")
    for i, (row, slot) in enumerate(zip(dataset["rows"], protocol["schedule"])):
        name, rep, arm = (slot[k] for k in ("case_id", "rep", "arm"))
        case = cases[name]
        require(row["sequence"] == i and all(row[k] == v for k, v in slot.items()) and
                row["exit_code"] == 0 and row["stdout_sha256"] == case["stdout_sha256"], "trial identity")
        require(all(uint(row[k]) for k in ("cpu_us", "user_us", "sys_us")) and
                row["cpu_us"] == row["user_us"] + row["sys_us"] > 0, "invalid CPU time")
        prefix = f"runs/{i:03d}/"
        files = {}
        for member, expected in row["receipts"].items():
            require(member.startswith(prefix), "receipt sequence")
            raw = contents.pop(member)
            require(sha(raw) == expected, f"changed receipt {member}")
            files[member[len(prefix):]] = raw
        require(files["stdout"] == case["stdout"].encode() and files["stderr"] == b"", "bad output")
        if arm == "warmup":
            require(rep == 0, "warmup in scored round")
            continue
        key = name, rep, arm
        pid = row["launcher_pid"]
        require(arm in arms and rep in REPS and key not in output, "duplicate or unexpected scored run")
        prod = READ_JSON(files["production.json"])
        repb = prod["rep_breakdown"]
        work = (prod["instructions"], repb["movs_bytes"], repb["stos_bytes"])
        require(prod["root_pid"] == pid and prod["complete"] is True and prod["valid"] is True
                and prod["authority"] == "diagnostic_only" and prod["integrity_verified"] is False
                and "latency_bound" not in prod and "memory_operand_bytes" not in prod and repb["version"] == 1
                and all(uint(v) for v in work) and work[0] > 0 and prod["rep_bytes"] == sum(work[1:])
                and sum(work) <= protocol["work_budget"] and struct.pack("<6Q", 1, 0, *work, 0) == files["clock.bin"],
                "wrong live report or terminal clock")
        obs = output[key] = {"cpu_ns": row["cpu_us"] * 1000, "work": dict(zip(WORK, work))}
        if arm != "live":
            obs["sites"] = read_sites(files["sample.bin" if arm == "packed" else "sample.json"], arm, pid, case)
    require(not contents and len(output) == len(cases) * len(arms) * len(REPS), "incomplete cohort")
    return output


def populations(cases):
    return {"repeat": [n for n, c in cases.items() if c["split"] == "repeat"],
            "holdout": [n for n, c in cases.items() if c["split"] == "holdout"], "all": list(cases)}


def cost(cases, rows, comparisons):
    results = {}
    groups = populations(cases)
    for base, candidate in comparisons:
        pairs = {n: [(rows[n, r, base]["cpu_ns"], rows[n, r, candidate]["cpu_ns"]) for r in REPS] for n in cases}
        by_case = {n: prior["paired_summary"](v) for n, v in pairs.items()}
        pooled = {group: prior["paired_summary"]([p for n in names for p in pairs[n]]) for group, names in groups.items()}
        result = {"base": base, "candidate": candidate, "by_case": by_case, "pooled": pooled}
        if candidate in ("packed", "profiled"):
            target, limit = (97, 102) if base == "full" else (105, 110)
            result["gates"] = {g: prior["decision"](pooled[g], {n: by_case[n] for n in ns}, target, limit)
                               for g, ns in groups.items()}
        results[f"{candidate}_vs_{base}"] = result
    return results


def coverage(values):
    observed, eligible = (sum(v[i] for v in values) for i in range(2))
    ratios = [Fraction(on, total) for on, total in values]
    return {"observations": len(values), "selected_visits": observed, "eligible_visits": eligible,
            "min_ppm": ppm(min(ratios)), "pooled_ppm": ppm(Fraction(observed, eligible)), "max_ppm": ppm(max(ratios)),
            "pass": all(10 * on >= 9 * total for on, total in values)}


def walk_checks(sites, oracle, cold):
    failures = []
    hot = oracle["offset"]
    if not {cold, hot} <= sites["catalog"]:
        failures.append("catalog_missing_walk_site")
    for arm in ("gate", "full", "packed", "profiled"):
        s = sites[arm].get(hot)
        if not s or (s["loads"], s["pairs"]) != (oracle["visits"], oracle["pairs"]):
            failures.append(arm + "_hot_counts")
        elif arm != "gate" and (s["near_pairs"] not in oracle["near_pairs_by_base_mod64_div4"] or
                               s["far_pairs"] != oracle["pairs"] - s["near_pairs"] or
                               s[SPAN] != oracle["sampled_span_bytes"]):
            failures.append(arm + "_hot_addresses")
        c = sites[arm].get(cold)
        if arm == "profiled":
            if c is not None:
                failures.append("profiled_includes_cold_site")
        elif not c or (c["loads"], c["pairs"]) != (1, 0):
            failures.append(arm + "_cold_counts")
        elif arm != "gate" and (c["near_pairs"], c["far_pairs"], c[SPAN]) != (0, 0, 0):
            failures.append(arm + "_cold_addresses")
    return failures


def check_sites(cases, rows, profiles, cold):
    failures = []
    cases_info = {}
    visits = {}
    for name, case in cases.items():
        selected = set(profiles["by_elf"][case["binary_sha256"]]["offsets"])
        visits[name] = []
        extra = []
        address_diffs = {"packed": Counter(), "profiled": Counter()}
        signature_matches = Counter()
        for rep in REPS:
            sets = {arm: rows[name, rep, arm]["sites"] for arm in ("catalog", "gate", "full", "packed", "profiled")}
            full = sets["full"]
            catalog = sets["catalog"]
            extra.append(len(catalog - full.keys()))
            missing = full.keys() - catalog
            if missing:
                failures.append({"case_id": name, "rep": rep, "missing_from_catalog": sorted(missing)})
            for arm in ("gate", "packed", "profiled"):
                reference = {o: s for o, s in full.items() if o in selected} if arm == "profiled" else full
                actual = sets[arm]
                changes = prior["signature"](reference, actual)
                if changes:
                    failures.append({"case_id": name, "rep": rep, "arm": arm, "site_signature_differences": changes})
                else:
                    signature_matches[arm] += 1
                if arm == "gate":
                    continue
                for offset in reference.keys() & actual.keys():
                    for field in ("near_pairs", "far_pairs", SPAN):
                        if actual[offset][field] != reference[offset][field]:
                            address_diffs[arm][offset, field] += 1
            visits[name].append((sum(s["loads"] for s in sets["profiled"].values()), sum(s["loads"] for s in full.values())))
            if "oracle" in case:
                problems = walk_checks(sets, case["oracle"], cold)
                if problems:
                    failures.append({"case_id": name, "rep": rep, "walk_oracle_failures": problems})
        cases_info[name] = {"exact_signatures": {arm: signature_matches[arm] for arm in ("gate", "packed", "profiled")},
                            "extra_catalog_offsets_per_rep": extra,
                            "descriptive_address_differences": {
                                arm: [{"offset": offset, "field": field, "differing_pairs": count}
                                      for (offset, field), count in sorted(diffs.items())]
                                for arm, diffs in address_diffs.items()}}
    return ({"pass": not failures, "cases": cases_info, "failures": failures},
            {"cases": {n: coverage(pairs) for n, pairs in visits.items()},
             "pooled": {g: coverage([p for n in ns for p in visits[n]]) for g, ns in populations(cases).items()}})


def work_diffs(cases, rows, comparisons):
    out = {}
    for base, candidate in comparisons:
        work = {}
        for name in cases:
            pairs = [(rows[name, r, base]["work"], rows[name, r, candidate]["work"]) for r in REPS]
            metrics = {}
            for field in WORK:
                deltas = [b[field]-a[field] for a, b in pairs]
                metrics[field] = {"min": min(deltas), "max": max(deltas), "equal_pairs": deltas.count(0)}
            work[name] = {"all_three_equal_pairs": sum(a == b for a, b in pairs), "signed_differences": metrics}
        out[f"{candidate}_vs_{base}"] = work
    return out


def evaluate(path, receipts):
    raw = path.read_bytes()
    raw = gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw
    data = json.loads(raw)
    protocol_raw = (HERE / "protocol.json").read_bytes()
    require(sha(protocol_raw) == data["protocol_sha256"] and data["schema"] == 1, "different protocol")
    protocol = json.loads(protocol_raw)
    frozen = {}
    for name in ("design", "profiles", "cases"):
        blob = (HERE / f"{name}.json").read_bytes()
        require(sha(blob) == protocol["tools_sha256"][f"{name}.json"], f"different frozen {name}")
        frozen[name] = json.loads(blob)
    design = frozen["design"]
    require(all(design[k + "_sha256"] == protocol["tools_sha256"][k + ".json"] for k in ("cases", "profiles")),
            "design and profile/case hashes disagree")
    cases = {c["case_id"]: c for c in frozen["cases"]}
    require(all(populations(cases)[g] == ns for g, ns in design["cases"].items() if g in ("repeat", "holdout")),
            "different case split")
    rows = observations(data, receipts, protocol, cases, design["arms"])
    integrity, cover = check_sites(cases, rows, frozen["profiles"], design["walk_oracles"]["cold_offset"])
    return {"schema": 1, "protocol_sha256": data["protocol_sha256"], "dataset_sha256": sha(raw),
            "receipts_sha256": sha(receipts.read_bytes()), "design_sha256": protocol["tools_sha256"]["design.json"],
            "integrity": integrity, "coverage": cover, "cost": cost(cases, rows, design["comparisons"]),
            "work_differences": work_diffs(cases, rows, design["comparisons"])}


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("dataset", type=Path)
    p.add_argument("receipts", type=Path)
    group = p.add_mutually_exclusive_group()
    group.add_argument("--output", type=Path)
    group.add_argument("--verify", type=Path)
    args = p.parse_args()
    value = evaluate(args.dataset, args.receipts)
    if args.verify:
        require(json.loads(args.verify.read_bytes()) == value, "saved evaluation differs")
        print("Verified site profiles, sampling fidelity, CPU costs and overhead gates.")
    elif args.output:
        args.output.write_text(json.dumps(value, indent=2) + "\n")
    else:
        print(json.dumps(value, indent=2))
