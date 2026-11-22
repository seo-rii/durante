#!/usr/bin/env python3
"""Replay first-use profiles, exact coverage, workflow costs and their amortization."""
import argparse
from fractions import Fraction
import gzip
import json
from pathlib import Path
import runpy
import struct
import tarfile

HERE = Path(__file__).resolve().parent
previous = runpy.run_path(str(HERE.parent / "live_sampler_cost/score.py"))
fast = runpy.run_path(str(HERE.parent / "live_sampler_fast/score.py"))
require, uint, sha, read_json = [previous[k] for k in ("require", "uint", "sha", "read_json")]
REPS = range(1, 7)
ARMS = ("live", "full", "profiled")
METRICS = ("workflow_cpu", "ordinary_baseline_cpu", "child_cpu", "workflow_wall")
SPAN = "sampled_address_span_bytes"


def intervals(row):
    for k in ("parent_cpu_ns", "hash_cpu_ns", "workflow_cpu_ns", "workflow_wall_ns", "cpu_us", "wall_us"):
        require(uint(row[k], 1), "missing operational accounting: " + k)
    for k in ("lookup_cpu_ns", "build_cpu_ns", "user_us", "sys_us"):
        require(uint(row[k]), "invalid component accounting: " + k)
    build, lookup, hashing = (row[k + "_cpu_ns"] for k in ("build", "lookup", "hash"))
    require((build > 0) == (row["arm"] == "calibration") and (lookup > 0) == (row["arm"] == "profiled"),
            "profile component on wrong arm")
    child = 1000 * (row["user_us"] + row["sys_us"])
    require(row["cpu_us"] * 1000 == child and child + row["parent_cpu_ns"] == row["workflow_cpu_ns"]
            and hashing + lookup + build <= row["parent_cpu_ns"] and
            row["workflow_wall_ns"] + 500 >= row["wall_us"] * 1000, "inconsistent timing intervals")
    return {"workflow_cpu": row["workflow_cpu_ns"], "child_cpu": child, "workflow_wall": row["workflow_wall_ns"],
            "ordinary_baseline_cpu": row["workflow_cpu_ns"] - (hashing if row["arm"] in ("live", "full") else 0),
            "parent_cpu_ns": row["parent_cpu_ns"], "hash_cpu_ns": hashing, "lookup_cpu_ns": lookup, "build_cpu_ns": build}


def read_rows(data, protocol, archive_path, cases):
    api = runpy.run_path(str(HERE / "profile.py"))
    profiles = {}
    held = {}
    calibrations = {}
    with tarfile.open(archive_path, "r|*") as archive:
        raw_files = {}
        for entry in archive:
            require(entry.isfile() and entry.name not in raw_files, "duplicate or invalid receipt")
            raw_files[entry.name] = archive.extractfile(entry).read()
    schedule = protocol["schedule"]
    require(len(data["rows"]) == len(schedule) == 186, "wrong trial count")
    for i, (row, slot) in enumerate(zip(data["rows"], schedule)):
        name, rep, arm = (slot[k] for k in ("case_id", "rep", "arm"))
        c = cases[name]
        elf = c["binary_sha256"]
        pid = row["launcher_pid"]
        require(row["sequence"] == i and all(row[k] == v for k, v in slot.items()) and row["elf_sha256"] == elf and
                row["exit_code"] == 0 and row["stdout_sha256"] == c["stdout_sha256"], "wrong execution identity")
        obs = intervals(row)
        prefix = f"runs/{i:03d}/"
        files = {}
        for n, digest in row["receipts"].items():
            raw = raw_files.pop(n)
            require(n.startswith(prefix) and sha(raw) == digest, f"wrong receipt {n}")
            files[n[len(prefix):]] = raw
        require(files["stdout"] == c["stdout"].encode() and files["stderr"] == b"", "wrong stdout/stderr")
        report = read_json(files["production.json"])
        repb = report["rep_breakdown"]
        work = (report["instructions"], repb["movs_bytes"], repb["stos_bytes"])
        require(report["root_pid"] == pid and report["complete"] is True and report["valid"] is True and
                report["authority"] == "diagnostic_only" and report["integrity_verified"] is False and
                "latency_bound" not in report and "memory_operand_bytes" not in report and
                all(uint(v) for v in work) and work[0] > 0 and repb["version"] == 1 and
                report["rep_bytes"] == sum(work[1:]) and sum(work) <= protocol["work_budget"] and
                files["clock.bin"] == struct.pack("<6Q", 1, 0, *work, 0), "wrong terminal live clock")
        supplied = set(row) & {"profile_path", "profile_sha256"}
        expected = {"profile_path", "profile_sha256"} if arm in ("calibration", "profiled") else set()
        require(supplied == expected, "profile binding fields on wrong arm")
        if arm != "live":
            obs["sites"] = previous["read_sidecar"](files["sample.json"], "full", pid, {})
        if arm == "calibration":
            require(c["split"] == "calibration" and rep == 0 and elf not in profiles and not held,
                    "invalid first-use calibration")
            raw = files["profile.json"]
            profile = json.loads(raw)
            regenerated = api["capture_for_digest"](elf, c["stdin"].encode(), files["sample.json"], pid, arm="full")
            require(profile == regenerated and sha(raw) == row["profile_sha256"]
                    and row["profile_path"] == prefix + "profile.json", "calibration profile lost its source binding")
            ranked = sorted(obs["sites"].items(), key=lambda kv: (-kv[1]["loads"], kv[0]))
            total = sum(s["loads"] for _, s in ranked)
            chosen, covered = [], 0
            for offset, site in ranked[:64]:
                chosen.append(offset)
                covered += site["loads"]
                if 100 * covered >= 95 * total:
                    break
            require(profile["offsets"] == sorted(chosen) and covered == profile["selected_visits"]
                    and total == profile["eligible_visits"], "profile does not implement the frozen ranking")
            profiles[elf] = profile, row["profile_path"], row["profile_sha256"]
            calibrations[c["program"]] = (name, obs)
        else:
            require(c["split"] == "holdout" and rep in REPS and arm in ARMS and (name, rep, arm) not in held
                    and elf in profiles, "unexpected held-out execution")
            if arm == "profiled":
                profile, n, digest = profiles[elf]
                require(row["profile_path"] == n and row["profile_sha256"] == digest
                        and api["resolve_for_digest"](profile, elf) == ",".join(map(str, profile["offsets"])),
                        "wrong cached profile on held-out execution")
            held[name, rep, arm] = obs
    require(len(held) == 180 and len(profiles) == len(calibrations) == 6 and not raw_files, "incomplete collection")
    return held, {k: v[0] for k, v in profiles.items()}, calibrations


def groups(cases):
    return {"real": [n for n, c in cases.items() if c["population"] == "real"],
            "control": [n for n, c in cases.items() if c["population"] == "control"],
            "same_path": ["path_switch/same"], "switched_path": ["path_switch/switch"], "all": list(cases)}


def control_oracle(sites, oracle, selected=None):
    active, inactive = oracle["offset"], oracle["inactive_offset"]
    if inactive in sites:
        return False
    if selected is not None and active not in selected:
        return active not in sites
    s = sites.get(active)
    return s is not None and (s["loads"], s["pairs"], s["near_pairs"], s["far_pairs"], s[SPAN]) == (
        oracle["visits"], oracle["pairs"], oracle["near_pairs"], oracle["far_pairs"], oracle[SPAN])


def fidelity(held, rows, profiles, calibration, all_cases):
    failures, by_case, coverage_pairs = [], {}, {}
    for n, obs in calibration.values():
        if "oracle" in all_cases[n] and not control_oracle(obs["sites"], all_cases[n]["oracle"]):
            failures.append({"case_id": n, "failure": "calibration control oracle"})
    for n, case in held.items():
        chosen = set(profiles[case["binary_sha256"]]["offsets"])
        coverage_pairs[n] = []
        address_differences = {}
        exact = 0
        for r in REPS:
            full = rows[n, r, "full"]["sites"]
            sampled = rows[n, r, "profiled"]["sites"]
            reference = {o: v for o, v in full.items() if o in chosen}
            differences = previous["signature"](reference, sampled)
            if differences:
                failures.append({"case_id": n, "rep": r, "projection": differences})
            else:
                exact += 1
            for o in sampled.keys() & reference.keys():
                for field in ("near_pairs", "far_pairs", SPAN):
                    if sampled[o][field] != reference[o][field]:
                        key = f"{o}/{field}"
                        address_differences[key] = address_differences.get(key, 0) + 1
            if "oracle" in case and (not control_oracle(full, case["oracle"])
                                    or not control_oracle(sampled, case["oracle"], chosen)):
                failures.append({"case_id": n, "rep": r, "failure": "held-out control oracle"})
            coverage_pairs[n].append((sum(v["loads"] for v in sampled.values()), sum(v["loads"] for v in full.values())))
        by_case[n] = {"coverage": fast["coverage"](coverage_pairs[n]), "exact_projection_pairs": exact,
                      "descriptive_address_differences_by_offset_and_field": address_differences}
    return {"projection_and_control_pass": not failures, "failures": failures, "cases": by_case,
            "pooled_coverage": {g: fast["coverage"]([p for n in ns for p in coverage_pairs[n]]) for g, ns in groups(held).items()}}


def full_observations(held, rows):
    def summarize(names):
        visits = {}
        per_pc_spans = {}
        spans = set()
        paired = set()
        for n in names:
            for r in REPS:
                for offset, site in rows[n, r, "full"]["sites"].items():
                    visits[offset] = visits.get(offset, 0) + site["loads"]
                    per_pc_spans.setdefault(offset, set()).add(site[SPAN])
                    spans.add(site[SPAN])
                    if site["pairs"]:
                        paired.add(offset)
        top = sorted(visits, key=lambda offset: (-visits[offset], offset))[:3]
        return {"processes": len(names) * len(REPS), "eligible_visits": sum(visits.values()),
                "distinct_executed_offsets": len(visits), "offsets_with_a_completed_pair": len(paired),
                "offsets_with_nonzero_span": sum(any(values) for values in per_pc_spans.values()),
                "distinct_span_byte_values": len(spans), "max_sampled_span_bytes": max(spans, default=0),
                "hottest_offsets": [{"offset": o, "visits": visits[o], "min_span_bytes": min(per_pc_spans[o]),
                                     "max_span_bytes": max(per_pc_spans[o])} for o in top]}
    return {"by_input": {n: summarize([n]) for n in held},
            "by_program": {p: summarize([n for n, c in held.items() if c["program"] == p])
                           for p in sorted({c["program"] for c in held.values()})}}


def costs(held, rows, comparisons):
    out = {}
    for base, candidate in comparisons:
        result = {}
        for metric in METRICS:
            pairs = {n: [(rows[n, r, base][metric], rows[n, r, candidate][metric]) for r in REPS] for n in held}
            case = {n: previous["paired_summary"](values) for n, values in pairs.items()}
            pool = {g: previous["paired_summary"]([p for n in ns for p in pairs[n]]) for g, ns in groups(held).items()}
            obj = {"cases": case, "pooled": pool}
            if metric in ("workflow_cpu", "ordinary_baseline_cpu") and candidate == "profiled":
                a, b = (97, 102) if base == "full" else (105, 110)
                obj["gates"] = {g: previous["decision"](pool[g], {n: case[n] for n in ns}, a, b)
                                for g, ns in groups(held).items() if g in ("real", "control")}
            if metric == "workflow_wall":
                for summary in [*case.values(), *pool.values()]:
                    for side in ("base", "candidate"):
                        summary[side + "_wall_ns"] = summary.pop(side + "_cpu_ns")
            result[metric] = obj
        out[candidate + "_vs_" + base] = result
    return out


def break_even(initial, savings):
    if savings <= 0:
        return None
    value = Fraction(initial, 1) / savings
    return -(-value.numerator // value.denominator)


def amortization(rows, calibration, held, integrity):
    result = {}
    singles = ((n, [n]) for n, c in held.items() if c["population"] == "control")
    mixtures = [(family, [n for n, c in held.items() if c["program"] == family]) for family in calibration]
    for label, names in [*mixtures, *singles]:
        first, initial = calibration[held[names[0]]["program"]]
        scenario = {"equal_weight_inputs": names, "calibration_input": first,
                    "all_inputs_meet_coverage": all(integrity["cases"][n]["coverage"]["pass"] for n in names)}
        for metric in ("workflow_cpu", "ordinary_baseline_cpu"):
            saved = [rows[n, r, "full"][metric]-rows[n, r, "profiled"][metric] for n in names for r in REPS]
            mean = Fraction(sum(saved), len(saved))
            startup = initial["build_cpu_ns"] + (initial["hash_cpu_ns"] if metric == "ordinary_baseline_cpu" else 0)
            subsequent = break_even(startup, mean)
            scenario[metric] = {"mean_savings_ns": str(mean), "online_extra_cpu_ns": startup,
                                "online_subsequent_inputs": subsequent,
                                "online_total_required_inputs": None if subsequent is None else subsequent + 1,
                                "standalone_extra_cpu_ns": initial["workflow_cpu"],
                                "standalone_required_inputs": break_even(initial["workflow_cpu"], mean)}
        result[label] = scenario
    return result


def evaluate(dataset, receipts):
    raw = dataset.read_bytes()
    raw = gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw
    data = json.loads(raw)
    plan_raw = (HERE / "protocol.json").read_bytes()
    require(sha(plan_raw) == data["protocol_sha256"] and data["schema"] == 1, "wrong protocol")
    plan = json.loads(plan_raw)
    for filename in ("design.json", "cases.json", "profile.py"):
        require(sha((HERE / filename).read_bytes()) == plan["tools_sha256"][filename], f"changed {filename}")
    design = json.loads((HERE / "design.json").read_bytes())
    cases = {c["case_id"]: c for c in json.loads((HERE / "cases.json").read_bytes())}
    held = {n: c for n, c in cases.items() if c["split"] == "holdout"}
    rows, profiles, calibration = read_rows(data, plan, receipts, cases)
    integrity = fidelity(held, rows, profiles, calibration, cases)
    components = {}
    for n in held:
        components[n] = {arm: {k: sum(rows[n, r, arm][k] for r in REPS) for k in
                                ("hash_cpu_ns", "lookup_cpu_ns", "parent_cpu_ns")} for arm in ARMS}
    first = {}
    for program, (n, obs) in calibration.items():
        p = profiles[cases[n]["binary_sha256"]]
        first[program] = {"calibration_case": n, "offsets": p["offsets"], "selected_visits": p["selected_visits"],
                          "eligible_visits": p["eligible_visits"], **{k: v for k, v in obs.items() if k != "sites"}}
    return {"schema": 1, "protocol_sha256": data["protocol_sha256"], "dataset_sha256": sha(raw),
            "receipts_sha256": sha(receipts.read_bytes()), "first_run": first, "heldout_component_sums": components,
            "fidelity": integrity, "full_observations": full_observations(held, rows),
            "cost": costs(held, rows, design["cost"]["comparisons"]),
            "amortization": amortization(rows, calibration, held, integrity)}


if __name__ == "__main__":
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
        print("Verified first-run profile construction, coverage, operational CPU costs and amortization.")
    elif args.output:
        args.output.write_text(json.dumps(result, indent=2) + "\n")
    else:
        print(json.dumps(result, indent=2))
