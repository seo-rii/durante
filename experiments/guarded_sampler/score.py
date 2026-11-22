#!/usr/bin/env python3
"""Replay the guarded decisions and compare delivered diagnostics with matched full runs."""
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
base = runpy.run_path(str(HERE.parent / "live_sampler_cost/score.py"))
first = runpy.run_path(str(HERE.parent / "first_run_sampler/score.py"))
profile_api = runpy.run_path(str(HERE.parent / "first_run_sampler/profile.py"))
guard_api = runpy.run_path(str(HERE / "guard.py"))
schedule = runpy.run_path(str(HERE / "schedule.py"))
require, uint, sha, ppm = [base[k] for k in ("require", "uint", "sha", "ppm")]
REPS = range(1, 9)
ARMS = ("live", "full", "profiled", "guarded")
SPAN = "sampled_address_span_bytes"


def classified(selected, omitted):
    total = selected + omitted
    return "use_profiled" if total > 0 and 10 * selected >= 9 * total else "run_full"


def groups(cases):
    return {"real": [n for n, c in cases.items() if c["population"] == "real"],
            "control": [n for n, c in cases.items() if c["population"] == "control"], "all": list(cases)}


def times(row):
    h, b, l, d, owner = (row[k + "_cpu_ns"] for k in ("hash", "build", "lookup", "decision", "parent"))
    child = 1000 * row["cpu_us"]
    arm = row["arm"]
    require(all(uint(v) for v in (h, b, l, d, owner, row["workflow_wall_ns"], row["cpu_us"],
                                   row["user_us"], row["sys_us"], row["workflow_cpu_ns"]))
            and h > 0 and h+b+l+d <= owner and child == 1000 * (row["user_us"] + row["sys_us"]) > 0
            and row["workflow_cpu_ns"] == child + owner and row["workflow_wall_ns"] > 0, "wrong CPU intervals")
    require((l > 0) == (arm in ("profiled", "guarded", "audit_guarded")) and
            (d > 0) == (arm in ("guarded", "audit_guarded")), "lookup or decision on wrong arm")
    return {"workflow_cpu": child + owner, "child_cpu": child, "workflow_wall": row["workflow_wall_ns"],
            "ordinary_baseline_cpu": child + owner - (h if arm in ("live", "full", "audit_fallback") else 0),
            "hash_cpu_ns": h, "lookup_cpu_ns": l, "decision_cpu_ns": d, "build_cpu_ns": b}


def read_observations(data, path, plan, cases):
    sources = runpy.run_path(str(HERE / "prepare.py"))["profiles_from_results"]()
    profiles = {elf: json.loads(raw) for elf, raw in sources.items()}
    bindings = {elf: ("profiles/" + elf + ".json", sha(raw)) for elf, raw in sources.items()}
    require(all(plan["tools_sha256"][p] == digest for p, digest in bindings.values()), "wrong #81 profiles")
    expected = schedule["effective_schedule"](plan, data["recovery"])
    require(len(expected) == len(data["rows"]), "incomplete schedule")
    with tarfile.open(path, "r|*") as archive:
        contents = {}
        for entry in archive:
            require(entry.isfile() and entry.name not in contents, "invalid archive")
            contents[entry.name] = archive.extractfile(entry).read()
    observations = {}
    for i, (slot, row) in enumerate(zip(expected, data["rows"])):
        n, r, arm = (slot[k] for k in ("case_id", "rep", "arm"))
        case = cases[n]
        elf = case["binary_sha256"]
        pid = row["launcher_pid"]
        require(row["sequence"] == i and all(row[k] == v for k, v in slot.items()) and row["elf_sha256"] == elf
                and row["exit_code"] == 0 and row["stdout_sha256"] == case["stdout_sha256"], "wrong execution")
        obs = times(row)
        files = {}
        prefix = f"runs/{i:03d}/"
        for member, digest in row["receipts"].items():
            raw = contents.pop(member)
            require(member.startswith(prefix) and sha(raw) == digest, "wrong receipt digest")
            files[member[len(prefix):]] = raw
        require(files["stdout"] == case["stdout"].encode() and files["stderr"] == b"", "bad stdout/stderr")
        prod = base["read_json"](files["production.json"])
        repb = prod["rep_breakdown"]
        work = prod["instructions"], repb["movs_bytes"], repb["stos_bytes"]
        require(prod["root_pid"] == pid and prod["quality"] == "uninterrupted" and prod["complete"] is True
                and prod["valid"] is True and prod["authority"] == "diagnostic_only" and prod["integrity_verified"] is False
                and all(uint(v) for v in work) and sum(work) <= plan["work_budget"]
                and files["clock.bin"] == struct.pack("<6Q", 1, 0, *work, 0), "wrong terminal clock")
        obs.update(sequence=i, pid=pid)
        if arm != "live":
            raw = files["sample.json"]
            obs.update(sites=base["read_sidecar"](raw, "full", pid, {}), sample_path=prefix + "sample.json", sample_sha=sha(raw))
        if arm in ("confirmation", "calibration"):
            checked = profile_api["capture_for_digest"](elf, case["stdin"].encode(), raw, pid, arm="full")
            if arm == "calibration":
                profile_raw = files["profile.json"]
                require(json.loads(profile_raw) == checked and elf not in profiles, "wrong new profile")
                profiles[elf] = checked
                bindings[elf] = prefix + "profile.json", sha(profile_raw)
            else:
                matches = checked["offsets"] == profiles[elf]["offsets"]
                require(row["confirmation_offsets"] == checked["offsets"] and row["confirmation_matches"] is matches,
                        "invalid confirmation observation")
                obs["confirmation"] = {"matches": matches, "confirmed_offsets": checked["offsets"],
                                       "archived_offsets": profiles[elf]["offsets"]}
                require((row["confirmation_profile_path"], row["confirmation_profile_sha256"]) == bindings[elf],
                        "wrong archived confirmation profile")
        if arm in ("profiled", "guarded", "audit_guarded", "calibration"):
            require((row["profile_path"], row["profile_sha256"]) == bindings[elf], "wrong runtime profile")
        if arm in ("guarded", "audit_guarded"):
            guard = base["read_json"](files["guard.json"])
            selected = sum(s["loads"] for s in obs["sites"].values())
            omitted = guard["omitted_loads"]
            decision = guard_api["decision"](base["read_json"](raw), guard, pid)
            require(decision == row["decision"] and decision["status"] == classified(selected, omitted), "wrong guard decision")
            obs["decision"] = decision
        require((n, r, arm) not in observations, "duplicate observation")
        observations[n, r, arm] = obs
    require(not contents, "unassigned receipts")
    return observations, {elf: p["offsets"] for elf, p in profiles.items()}


def control_matches(case, sites, offsets=None):
    if "mix_oracle" in case:
        return sites == {s["offset"]: s for s in case["mix_oracle"] if offsets is None or s["offset"] in offsets}
    return "oracle" not in case or first["control_oracle"](sites, case["oracle"], offsets)


def integrity(cases, rows, profiles):
    failures, summaries = [], {}
    for name, case in cases.items():
        offsets = set(profiles[case["binary_sha256"]])
        if case["split"] == "calibration":
            arm = "calibration" if case["program"] == "path_mix" else "confirmation"
            if not control_matches(case, rows[name, 0, arm]["sites"]):
                failures.append({"case_id": name, "error": "first-input oracle"})
            if "mix_oracle" in case and offsets != {case["mix_oracle"][0]["offset"]}:
                failures.append({"case_id": name, "error": "wrong frequency-shift selection"})
            continue
        selected_values, omitted_values, full_values, ratios, statuses = [], [], [], [], []
        addr_diffs = {"guarded": Counter(), "profiled": Counter()}
        matches = Counter()
        for rep in REPS:
            full = rows[name, rep, "full"]["sites"]
            guard = rows[name, rep, "guarded"]
            d = guard["decision"]
            exp = {o: s for o, s in full.items() if o in offsets}
            expected_s = sum(s["loads"] for s in exp.values())
            expected_o = sum(s["loads"] for o, s in full.items() if o not in offsets)
            expected_status = classified(expected_s, expected_o)
            selected_values.append(d["selected_visits"])
            omitted_values.append(d["omitted_visits"])
            full_values.append(expected_s + expected_o)
            ratios.append(Fraction(d["selected_visits"], d["total_visits"]) if d["total_visits"] else None)
            statuses.append(d["status"])
            if ((d["selected_visits"], d["omitted_visits"]) != (expected_s, expected_o) or d["status"] != expected_status):
                failures.append({"case_id": name, "rep": rep, "full_counts": [expected_s, expected_o],
                                 "guard_counts": [d["selected_visits"], d["omitted_visits"]]})
            if not control_matches(case, full):
                failures.append({"case_id": name, "rep": rep, "error": "full control oracle"})
            for arm in ("guarded", "profiled"):
                actual = rows[name, rep, arm]["sites"]
                diff = base["signature"](exp, actual)
                if diff or not control_matches(case, actual, offsets):
                    failures.append({"case_id": name, "rep": rep, "arm": arm, "projection_differences": diff})
                else:
                    matches[arm] += 1
                for o in actual.keys() & exp.keys():
                    for field in ("near_pairs", "far_pairs", SPAN):
                        if actual[o][field] != exp[o][field]:
                            addr_diffs[arm][f"{o}/{field}"] += 1
        if name in schedule["AUDITS"]:
            expected = "use_profiled" if name == "path_switch/same" else "run_full"
            if any(s != expected for s in statuses):
                failures.append({"case_id": name, "error": "wrong controlled-workload decision"})
        valid = [v for v in ratios if v is not None]
        summaries[name] = {"use_profiled": statuses.count("use_profiled"), "run_full": statuses.count("run_full"),
                           "guarded_selected_visits": selected_values, "guarded_omitted_visits": omitted_values,
                           "matched_full_visits": full_values, "min_coverage_ppm": ppm(min(valid)) if valid else None,
                           "max_coverage_ppm": ppm(max(valid)) if valid else None, "exact_signatures": dict(matches),
                           "descriptive_address_differences": {k: dict(v) for k, v in addr_diffs.items()}}
    return {"pass": not failures, "failures": failures, "cases": summaries}


def measured(cases, rows, comparisons):
    groups_by_name = groups(cases)
    all_results = {}
    for base_arm, on_arm in comparisons:
        result = {}
        for metric in first["METRICS"]:
            pairs = {n: [(rows[n, r, base_arm][metric], rows[n, r, on_arm][metric]) for r in REPS] for n in cases}
            by_case = {n: base["paired_summary"](p) for n, p in pairs.items()}
            pooled = {g: base["paired_summary"]([p for n in ns for p in pairs[n]]) for g, ns in groups_by_name.items()}
            item = {"by_case": by_case, "pooled": pooled}
            if metric in ("workflow_cpu", "ordinary_baseline_cpu") and on_arm == "guarded" and base_arm in ("live", "full"):
                a, b = (97, 102) if base_arm == "full" else (105, 110)
                item["gates"] = {g: base["decision"](pooled[g], {n: by_case[n] for n in ns}, a, b)
                                 for g, ns in groups_by_name.items() if g in ("real", "control")}
            if metric == "workflow_wall":
                for summary in [*by_case.values(), *pooled.values()]:
                    for side in ("base", "candidate"):
                        summary[side + "_wall_ns"] = summary.pop(side + "_cpu_ns")
            result[metric] = item
        all_results[on_arm + "_vs_" + base_arm] = result
    return all_results


def projected(cases, rows):
    pairs = {}
    for n in cases:
        values = []
        for r in REPS:
            full, guarded = rows[n, r, "full"], rows[n, r, "guarded"]
            recovered = guarded["workflow_cpu"]
            if guarded["decision"]["status"] == "run_full":
                recovered += full["workflow_cpu"]
            values.append((full["workflow_cpu"], recovered))
        pairs[n] = values
    return {"by_case": {n: base["paired_summary"](p) for n, p in pairs.items()},
            "pooled": {g: base["paired_summary"]([p for n in names for p in pairs[n]]) for g, names in groups(cases).items()}}


def audits(recovery, cases, rows, profiles):
    out = {}
    for n in schedule["AUDITS"]:
        info = recovery[n]
        guard = rows[n, 0, "audit_guarded"]
        d = guard["decision"]
        case = cases[n]
        selected = profiles[case["binary_sha256"]]
        require(control_matches(case, guard["sites"], selected), "invalid audit guard control oracle")
        fallback = rows.get((n, 0, "audit_fallback"))
        require((fallback is not None) == (d["status"] == "run_full"), "incorrect audit rerun")
        source, delivered = ("full", fallback) if fallback else ("guarded", guard)
        require(info["guarded_sequence"] == guard["sequence"] and info["decision"] == d and
                info["fallback_sequence"] == (fallback["sequence"] if fallback else None)
                and info["selected_sequence"] == delivered["sequence"] and info["final_source"] == source
                and info["final_receipt"] == delivered["sample_path"]
                and info["final_receipt_sha256"] == delivered["sample_sha"], "wrong diagnostic delivery receipt")
        if fallback:
            full = fallback["sites"]
            expected_s = sum(s["loads"] for o, s in full.items() if o in selected)
            expected_o = sum(s["loads"] for o, s in full.items() if o not in selected)
            require(fallback["pid"] != guard["pid"] and control_matches(case, full) and
                    (d["selected_visits"], d["omitted_visits"]) == (expected_s, expected_o), "invalid recovery oracle")
        require((source == "guarded") == (n == "path_switch/same"), "control audit delivered wrong source")
        out[n] = {"decision": d, "delivered_source": source, "guarded_pid": guard["pid"],
                  "fallback_pid": fallback["pid"] if fallback else None,
                  "guarded_workflow_cpu_ns": guard["workflow_cpu"],
                  "fallback_workflow_cpu_ns": fallback["workflow_cpu"] if fallback else 0,
                  "total_workflow_cpu_ns": guard["workflow_cpu"] + (fallback["workflow_cpu"] if fallback else 0)}
    return out


def evaluate(dataset, receipts):
    raw = dataset.read_bytes()
    raw = gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw
    data = json.loads(raw)
    protocol_raw = (HERE / "protocol.json").read_bytes()
    require(sha(protocol_raw) == data["protocol_sha256"], "wrong protocol")
    plan = json.loads(protocol_raw)
    for n in ("cases.json", "design.json", "guard.py"):
        require(sha((HERE / n).read_bytes()) == plan["tools_sha256"][n], "changed " + n)
    design = json.loads((HERE / "design.json").read_bytes())
    require(design["cases_sha256"] == plan["tools_sha256"]["cases.json"], "wrong frozen cohort")
    cases = {c["case_id"]: c for c in json.loads((HERE / "cases.json").read_bytes())}
    rows, profiles = read_observations(data, receipts, plan, cases)
    held = {n: c for n, c in cases.items() if c["split"] == "holdout"}
    return {"schema": 1, "protocol_sha256": data["protocol_sha256"], "dataset_sha256": sha(raw),
            "receipts_sha256": sha(receipts.read_bytes()),
            "profile_confirmations": {n: obs["confirmation"] for (n, _, a), obs in rows.items() if a == "confirmation"},
            "integrity": integrity(cases, rows, profiles),
            "cost": measured(held, rows, design["cost"]["comparisons"]), "projected_recovery_vs_full": projected(held, rows),
            "observed_recovery_audits": audits(data["recovery"], cases, rows, profiles)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("receipts", type=Path)
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--output", type=Path)
    output.add_argument("--verify", type=Path)
    args = parser.parse_args()
    evaluation = evaluate(args.dataset, args.receipts)
    if args.verify:
        require(json.loads(args.verify.read_bytes()) == evaluation, "saved evaluation differs")
        print("Verified the sampled projections, guarded decisions, costs and fallback workflows.")
    elif args.output:
        args.output.write_text(json.dumps(evaluation, indent=2) + "\n")
    else:
        print(json.dumps(evaluation, indent=2))
