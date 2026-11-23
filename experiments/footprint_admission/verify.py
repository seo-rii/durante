"""Replay paid-anchor selection, real enforced limits and independent wait4 references from a pinned guest."""
import argparse
from collections import Counter, defaultdict
from fractions import Fraction
import json
from pathlib import Path
from statistics import median
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from calibrated_limits.verify import METER, TYPES, live_observation, load, positive, verify_placement
from cpp20_locality.verify import release_reader, require, sha
from footprint_admission import admission
from guarded_native import anchor
from guarded_native.score import MS, boundary
from shape_calibration import combined, shape, temporal
from shape_calibration import verify as prior

METHODS = ("temporal", "footprint")


def select(method, bank, profiles, current):
    full, order = current
    attempts = []
    for index, name in enumerate(bank):
        old_full, old_order = profiles[name]
        spatial = shape.compare(old_full, full)
        clock_order = temporal.compare(old_order, order)
        row = dict(reference_index=index, shape=spatial, temporal=clock_order)
        gates = [spatial, clock_order]
        if method == "footprint":
            row["footprint"] = admission.compare(old_full, full, spatial)
            gates.append(row["footprint"])
        attempts.append(row)
        if all(gate["status"] == "reuse" for gate in gates):
            return index, attempts
    return None, attempts


def read_choices(directory, case, row, banks, profiles, policies, current, kernel, register):
    name = case["case_id"]
    obs = current[0]["observation"]
    choices = {method: select(method, banks[method], profiles, current) for method in METHODS}
    require(set(row["policies"]) == set(METHODS), "unexpected method in selection summary")
    paid = any(index is None for index, _ in choices.values())
    require((directory / "policy.json").exists() == paid, "physical native anchor differs from admission")
    if paid:
        policies[name] = prior.paid_policy(directory, obs, case, kernel)
        register(policies[name]["native_pid"], (case["program"],))
    results = {}
    for method, (index, attempts) in choices.items():
        bank = banks[method]
        selected = name if index is None else bank[index]
        clock = policies[selected]
        action = "calibrate" if index is None else "reuse"
        prediction = prior.estimate(clock, obs)
        expected = dict(schema=1, method=method, action=action, reference_index=index, policy_sha256=clock["hash"],
                        prediction_ns=prediction, comparisons=attempts, current_sample_sha256=obs["sample_sha256"])
        path = directory / f"{method}-selection.json"
        require(load(path) == expected, f"selection disagrees with chronological replay: {name} {method}")
        compact = dict(anchor_case=selected, action=action, prediction_ns=prediction)
        require(row["policies"][method] == compact, "clock selection summary differs from replay")
        reasons = [{k: v["reasons"] for k, v in detail.items() if k != "reference_index"} for detail in attempts]
        results[method] = dict(**compact, reference_index=index, selection_sha256=sha(path.read_bytes()),
                               compared=dict(zip(bank[:len(attempts)], reasons, strict=True)),
                               paid_native_ns=clock["native_ns"] if index is None else 0, live=[])
        if index is None:
            bank.append(name)
    return results


def check_limits(directory, seq, case, row, plan, observations, policies, obs, reader, register):
    schedule = [(index, ms, method) for index, ms in enumerate(case["limits_ms"])
                for method in (METHODS if (seq + index) % 2 == 0 else METHODS[::-1])]
    for slot, (index, ms, method) in zip(row["limits"], schedule, strict=True):
        require((slot["index"], slot["limit_ms"], slot["method"]) == (index, ms, method),
                "wrong enforced limit or method order")
        arm = observations[method]
        policy = policies[arm["anchor_case"]]
        dest = directory / "limits" / method / f"{index:02d}"
        raw = load(dest / "live/result.json")
        wrapped = {**raw, "selection_sha256": arm["selection_sha256"], "method": method, "action": arm["action"],
                   "reference_index": arm["reference_index"], "current_sample_sha256": obs["sample_sha256"],
                   "current_result_sha256": obs["result_sha256"], "preflight_prediction_ns": arm["prediction_ns"]}
        require(load(dest / "result.json") == wrapped, "wrapper differs from selected capture, policy, or live run")
        result = live_observation(dest / "live", {**slot, "kind": "limited"}, case, plan, policy, reader)
        register(result["pid"], METER)
        arm["live"].append(dict(limit_ms=ms, pid=result["pid"], observation=result["observation"], tle=result["tle"],
                                prediction_ns=result["prediction_ns"], wall_ns=result["wall_ns"]))


def score_case(case, trial, samples):
    center = median(samples)
    result = dict(cohort=case["cohort"], shape_label=case["shape_label"], profile_cpu_ns=trial["profile_cpu_ns"],
                  native_ns=samples, min_ns=min(samples), median_ns=center, max_ns=max(samples),
                  native_range_ppm=round(Fraction(max(samples) - min(samples), center) * MS))
    for method in METHODS:
        arm = trial[method]
        preflight = prior.score_prediction(arm["prediction_ns"], samples, case["limits_ms"])
        scored = []
        for entry in arm["live"]:
            verdict = boundary(samples, [entry["prediction_ns"]], entry["limit_ms"])
            require(bool(verdict["model_tle_reps"]) == entry["tle"], "cutoff status differs from live observation")
            scored.append(dict(**entry, native_class=verdict["classification"], native_exceeded=verdict["native_tle_reps"],
                               agrees_with_preflight=entry["tle"] == (arm["prediction_ns"] > entry["limit_ms"] * MS)))
        result[method] = {**arm, **preflight, "live": scored}
    return result


def summarize(cases):
    profile = sum(case["profile_cpu_ns"] for case in cases)
    result = dict(inputs=len(cases), combined_profile_cpu_ns=profile)
    for method in METHODS:
        arms = [case[method] for case in cases]
        live = [entry for arm in arms for entry in arm["live"]]
        classes = Counter(entry["native_class"] for entry in live)
        preflight = Counter(entry["classification"] for arm in arms for entry in arm["limits"])
        paid = sum(arm["paid_native_ns"] for arm in arms)
        errors = [abs(arm["signed_error_ppm"]) for arm in arms]
        result[method] = dict(within_20pct=sum(arm["within_20pct"] for arm in arms),
                              median_absolute_error_ppm=median(errors), max_absolute_error_ppm=max(errors),
                              new_native_anchors=sum(arm["action"] == "calibrate" for arm in arms),
                              reused_anchors=sum(arm["action"] == "reuse" for arm in arms), paid_native_ns=paid,
                              preflight_cpu_ns=profile + paid, limits=len(live),
                              preflight_verdicts={k: preflight[k] for k in TYPES},
                              live_verdicts={k: classes[k] for k in TYPES},
                              live_kills=sum(entry["observation"] == "live" for entry in live),
                              live_tle=sum(entry["tle"] for entry in live),
                              preflight_verdict_mismatches=sum(not entry["agrees_with_preflight"] for entry in live))
    paired = [(a, b) for case in cases for a, b in zip(case["temporal"]["live"], case["footprint"]["live"], strict=True)]
    result["changed_live_verdicts"] = sum(a["tle"] != b["tle"] for a, b in paired)
    result["native_mixed_limits"] = sum(a["native_class"] == "mixed_native" for a, _ in paired)
    return result


def verify(root):
    plan, rules, summary = (load(path) for path in (HERE / "plan.json", HERE / "rules.json", root / "summary.json"))
    require(summary["schema"] == plan["schema"] == rules["schema"] == 1
            and summary["plan_sha256"] == sha((HERE / "plan.json").read_bytes())
            and summary["rules_sha256"] == sha((HERE / "rules.json").read_bytes())
            and summary["probe_sha256"] == rules["probe_sha256"] == temporal.PROBE_SHA
            and summary["affinity"] == [1] and summary["cgroup_cpus"] == "1" and isinstance(summary["kernel"], str)
            and summary["kernel"] and type(summary["steal_delta"]) is int and summary["steal_delta"] >= 0,
            "different registration, sampler or guest CPU placement")
    reader = release_reader(load(anchor.PROTOCOL)["tools_sha256"]["production_reader.py"])
    pids = {}

    def register(pid, commands):
        require(positive(pid) and pid not in pids, "duplicate measured PID")
        pids[pid] = commands

    profiles, policies, trials, starters = {}, {}, {}, {}
    banks = defaultdict(lambda: {method: [] for method in METHODS})
    cases = plan["cases"]
    for seq, (case, row) in enumerate(zip(cases, summary["rows"], strict=True)):
        name, elf = case["case_id"], case["binary_sha256"]
        require(row["sequence"] == seq and name == row["case_id"] and name not in trials
                and (case["split"] == "calibration") == (elf not in banks), "wrong case or starter order")
        raw_input = case["stdin"].encode()
        require((root / "inputs" / sha(raw_input)).read_bytes() == raw_input, "incorrect registered input")
        directory = root / "cases" / name
        full = prior.check_full(case, directory / "full")
        obs = full["observation"]
        order = combined.inspect_temporal(directory, elf, sha(raw_input), sha(case["stdout"].encode()), temporal.PROBE_SHA)
        cpu = order["observation"]["cpu_ns"]
        require(row["profile_cpu_ns"] == cpu == prior.profile_cpu_ns(directory / "full"), "wrong combined profile charge")
        require(positive(row["profile_pid"]) and row["profile_pid"] == obs["pid"], "wrong combined profile PID")
        register(obs["pid"], METER)
        current = (full, order)
        arms = read_choices(directory, case, row, banks[elf], profiles, policies, current, summary["kernel"], register)
        check_limits(directory, seq, case, row, plan, arms, policies, obs, reader, register)
        profiles[name] = current
        trials[name] = dict(**arms, profile_cpu_ns=cpu)
        if case["split"] == "calibration":
            starters[name] = dict(profile_cpu_ns=cpu, paid_native_ns=policies[name]["native_ns"])

    heldout = sorted((c for c in cases if c["split"] == "holdout"), key=lambda c: sha(c["case_id"].encode()))
    samples = prior.native_reference(root, summary, plan, heldout, register)
    verify_placement(root, pids)
    scored = {c["case_id"]: score_case(c, trials[c["case_id"]], samples[c["case_id"]]) for c in heldout}
    totals = {group: summarize([v for v in scored.values() if group == "all" or v["cohort"] == group])
              for group in ("previous", "prospective", "all")}
    return dict(schema=1, plan_sha256=summary["plan_sha256"], rules_sha256=summary["rules_sha256"],
                probe_sha256=summary["probe_sha256"], summary_sha256=sha((root / "summary.json").read_bytes()),
                cpu=1, steal_ticks=summary["steal_delta"], pids_verified=len(pids), native_runs=len(summary["native"]),
                physical_native_calibrations=len(policies),
                physical_native_calibration_cpu_ns=sum(p["native_ns"] for p in policies.values()),
                initial_cost_ns=starters, totals=totals, cases=scored)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="extracted guest output directory")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    metrics = verify(args.directory)
    if args.output:
        args.output.write_text(json.dumps(metrics, indent=2) + "\n")
    print(json.dumps(metrics["totals"], indent=2))
