"""Replay shape admission, charged executions, and prospective CPU-time estimates from guest receipts."""
import argparse
from collections import Counter, defaultdict
from fractions import Fraction
import json
from math import ceil
from pathlib import Path
from statistics import median
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from calibrated_limits.verify import METER, TYPES, live_observation, load, positive, verify_placement, wait4_ns
from cpp20_locality.verify import release_reader, require, sha, verify_counters
from guarded_native import anchor
from guarded_native.score import MS, boundary
from shape_calibration import shape, temporal


def profile_cpu_ns(directory):
    launches = load(directory / "result.json")["launches"]
    row, = launches
    require(row["mode"] == "full" and positive(row["cpu_us"])
            and all(type(row[k]) is int and row[k] >= 0 for k in ("user_us", "sys_us"))
            and row["user_us"] + row["sys_us"] == row["cpu_us"], "invalid full-profile wait4 time")
    return row["cpu_us"] * 1000


def score_prediction(estimate, samples, limits_ms):
    center = median(samples)
    signed = Fraction(estimate - center, center)
    return {
        "prediction_ns": estimate, "signed_error_ppm": round(signed * MS),
        "within_20pct": 5 * abs(estimate - center) <= center,
        "limits": [boundary(samples, [estimate], ms) for ms in limits_ms],
    }


def summarize(rows):
    plain = sum(r["plain_meter_cpu_ns"] for r in rows)
    result = {"inputs": len(rows), "paired_plain_meter_cpu_ns": plain}
    for method in ("always_reuse", "shape", "temporal"):
        scores = [r[method] for r in rows]
        decisions = [d for s in scores for d in s["limits"]]
        tally = Counter(d["classification"] for d in decisions)
        errors = [abs(s["signed_error_ppm"]) for s in scores]
        full = sum(r["full_profile_cpu_ns"] for r in rows) if method in ("shape", "temporal") else 0
        temporal = sum(r["temporal_profile_cpu_ns"] for r in rows) if method == "temporal" else 0
        native = sum(s["paid_native_ns"] for s in scores)
        result[method] = {
            "within_20pct": sum(s["within_20pct"] for s in scores),
            "median_absolute_error_ppm": median(errors), "max_absolute_error_ppm": max(errors),
            "limits": len(decisions), **{key: tally[key] for key in TYPES},
            "new_native_anchors": sum(s["action"] == "calibrate" for s in scores),
            "reused_anchors": sum(s["action"] == "reuse" for s in scores),
            "reused_outside_20pct": sum(s["action"] == "reuse" and not s["within_20pct"] for s in scores),
            "full_profile_cpu_ns": full, "temporal_profile_cpu_ns": temporal, "paid_native_ns": native,
            "profile_plus_new_native_cpu_ns": full + temporal + native,
            "preflight_to_plain_target_cpu_ratio": str(Fraction(full + temporal + native, plain)),
        }
    actual = [d for r in rows for d in r["live"]]
    decisions = Counter(d["native_class"] for d in actual)
    result["live"] = {"limits": len(actual), **{key: decisions[key] for key in TYPES},
                      "live_kills": sum(d["observation"] == "live" for d in actual),
                      "terminal_exceeded": sum(d["observation"] == "terminal" and d["tle"] for d in actual),
                      "completed_within": sum(not d["tle"] for d in actual),
                      "control_disagreements": sum(not d["agrees_with_complete"] for d in actual)}
    result["native_reference_median_ns"] = sum(r["median_ns"] for r in rows)
    return result


def check_full(case, directory):
    result = shape.inspect_run(directory, case["binary_sha256"])
    obs = result["observation"]
    require(obs["launch_count"] == 1 and obs["mode"] == "full"
            and obs["stdin_sha256"] == sha(case["stdin"].encode())
            and obs["stdout_sha256"] == sha(case["stdout"].encode()),
            "different full-profile input, output or launch for " + case["case_id"])
    return result


def native_reference(root, summary, plan, holdout, register):
    schedule = [(r + 1, case) for r in range(plan["native_rounds"])
                for case in holdout[r % len(holdout):] + holdout[:r % len(holdout)]]
    samples = defaultdict(list)
    for seq, (row, (rep, case)) in enumerate(zip(summary["native"], schedule, strict=True)):
        require(row["sequence"] == seq and row["round"] == rep and row["case_id"] == case["case_id"],
                "different native reference order")
        register(row["pid"], (case["program"],))
        directory = root / "native" / f"{seq:03d}"
        require((directory / "stdout").read_bytes() == case["stdout"].encode()
                and row["stdout_sha256"] == sha(case["stdout"].encode()) and (directory / "stderr").stat().st_size == 0,
                "wrong direct native output: " + case["case_id"])
        samples[case["case_id"]].append(wait4_ns(row))
    return samples


def check_native(row, case, kernel):
    require(row["affinity"] == [1] and row["single_cpu"] is True and row["kernel"] == kernel
            and row["machine"] == "x86_64" and row["stdout_sha256"] == sha(case["stdout"].encode()),
            "wrong fallback native placement or output")
    return wait4_ns(row)


def paid_policy(directory, observation, case, kernel):
    path = directory / "policy.json"
    saved = load(path)
    require(saved["schema"] == 1 and saved["authority"] == "diagnostic_only"
            and saved["reference_sha256"] == anchor.REFERENCE_SHA and saved["combined_reference_weights"] == anchor.weights()
            and saved["anchor"] == observation and saved["calibration_sha256"] == anchor.calibration_digest(observation, saved["native"]),
            "calibration policy differs from terminal receipt or frozen weights")
    native = saved["native"]
    weights = {k: Fraction(v) for k, v in saved["combined_reference_weights"].items()}
    return {"hash": sha(path.read_bytes()), "native_ns": check_native(native, case, kernel),
            "native_pid": native["pid"], "weights": weights,
            "work": sum(w * observation[k] for k, w in weights.items())}


def estimate(policy, observation):
    candidate = sum(w * observation[k] for k, w in policy["weights"].items())
    require(candidate > 0 and policy["work"] > 0, "empty observed work")
    return ceil(policy["native_ns"] * candidate / policy["work"])


def score_case(case, trial, references):
    center = median(references)
    metrics = {"cohort": case["cohort"], "shape_label": case["shape_label"],
               "native_ns": references, "min_ns": min(references), "median_ns": center, "max_ns": max(references),
               "full_profile_cpu_ns": trial["full_ns"], "temporal_profile_cpu_ns": trial["temporal_ns"],
               "plain_meter_cpu_ns": trial["plain_ns"], "live": [],
               "selected_prediction_from_plain_ns": trial["plain_prediction_ns"]}
    for method, arm in trial["policies"].items():
        scored = score_prediction(arm["prediction_ns"], references, case["limits_ms"])
        scored.update(anchor_case=arm["anchor_case"], action=arm["action"],
                      paid_native_ns=trial["native_ns"] if arm["action"] == "calibrate" else 0)
        if method != "always_reuse":
            scored["comparisons"] = [
                {"anchor_case": attempted, "shape_reasons": comparison["shape"]["reasons"],
                 "temporal_reasons": comparison["temporal"]["reasons"] if comparison["temporal"] else None}
                for attempted, comparison in trial["compared"][method]
            ]
        metrics[method] = scored
    for obs in trial["live"]:
        threshold = obs["limit_ms"] * MS
        verdict = boundary(references, [obs["prediction_ns"]], obs["limit_ms"])
        metrics["live"].append({"limit_ms": obs["limit_ms"], "prediction_ns": obs["prediction_ns"],
                                 "observation": obs["observation"], "tle": obs["tle"], "pid": obs["pid"],
                                 "wall_ns": obs["wall_ns"], "native_exceeded": verdict["native_tle_reps"],
                                 "native_class": verdict["classification"],
                                 "agrees_with_complete": obs["tle"] == (trial["plain_prediction_ns"] > threshold)})
    return metrics


def bank_decision(method, names, history, current):
    rows = []
    for index, name in enumerate(names):
        prior = history[name]
        base = shape.compare(prior["profile"], current["profile"])
        extra = temporal.compare(prior["temporal"], current["temporal"]) if method == "temporal" else None
        rows.append({"reference_index": index, "shape": base, "temporal": extra})
        if base["status"] == "reuse" and (extra is None or extra["status"] == "reuse"):
            return index, rows
    return None, rows


def check_plain(directory, case, reader):
    row = load(directory / "result.json")
    pid = row["launcher_pid"]
    require(positive(pid) and row["exit_code"] == 0 and positive(row["clock_polls"])
            and row["stdout_sha256"] == sha(case["stdout"].encode())
            and (directory / "stdout").read_bytes() == case["stdout"].encode()
            and (directory / "stderr").stat().st_size == 0, "wrong completed plain-meter output")
    record = directory / "records" / f"process-{pid}.jsonl"
    require(set(record.parent.iterdir()) == {record}, "different plain-meter process")
    production = load(directory / "production.json")
    verify_counters(record.read_bytes(), production, (directory / "clock.bin").read_bytes(), "live", pid,
                    load(anchor.PROTOCOL)["work_budget"], reader)
    cpu_ns = wait4_ns(dict(row, cpu_ns=row["cpu_us"] * 1000))
    return pid, cpu_ns, production


def verify(root):
    plan = load(HERE / "plan.json")
    summary = load(root / "summary.json")
    require(summary["schema"] == plan["schema"] == 1 and summary["plan_sha256"] == sha((HERE / "plan.json").read_bytes())
            and summary["rules_sha256"] == sha((HERE / "rules.json").read_bytes()) and summary["affinity"] == [1]
            and summary["cgroup_cpus"] == "1" and isinstance(summary["kernel"], str) and summary["kernel"]
            and type(summary["steal_delta"]) is int and summary["steal_delta"] >= 0,
            "different plan, admission rule or CPU placement")
    probe_sha = summary["probe_sha256"]
    require(probe_sha == load(HERE / "rules.json")["probe_sha256"] == temporal.PROBE_SHA,
            "missing temporal probe identity")
    reader = release_reader(load(anchor.PROTOCOL)["tools_sha256"]["production_reader.py"])
    pids = {}
    def register(pid, commands):
        require(positive(pid) and pid not in pids, "duplicate measured PID in the collection")
        pids[pid] = commands

    by_name = {}
    banks = defaultdict(lambda: {"shape": [], "temporal": []})
    initial = {}
    trials = {}
    cases = plan["cases"]
    for seq, (case, row) in enumerate(zip(cases, summary["rows"], strict=True)):
        name, program, digest = case["case_id"], case["program"], case["binary_sha256"]
        require(name not in by_name and row["sequence"] == seq and row["case_id"] == name
                and case["split"] in ("calibration", "holdout") and (program not in initial) == (case["split"] == "calibration"),
                "unexpected first or held-out case in collection order")
        raw_input = case["stdin"].encode()
        require((root / "inputs" / sha(raw_input)).read_bytes() == raw_input, "different registered input file")
        by_name[name] = case
        directory = root / "cases" / name
        full = check_full(case, directory / "full")
        observation = full["observation"]
        full_ns = profile_cpu_ns(directory / "full")
        require(row["profile_pid"] == observation["pid"] and row["profile_cpu_ns"] == full_ns,
                "full-profile PID or CPU cost mismatch")
        register(row["profile_pid"], METER)
        extra = temporal.inspect_run(directory / "temporal", digest, sha(case["stdin"].encode()), sha(case["stdout"].encode()), probe_sha)
        temporal_row = extra["observation"]
        require(row["temporal_pid"] == temporal_row["pid"] and row["temporal_cpu_ns"] == wait4_ns(temporal_row),
                "temporal-profile PID or CPU cost mismatch")
        register(row["temporal_pid"], METER)
        current = {"profile": full, "temporal": extra}
        prior_banks = banks[program]
        choices = {
            method: bank_decision(method, prior_banks[method], trials, current)
            for method in ("shape", "temporal")
        }
        needs_native = any(choice[0] is None for choice in choices.values())
        require((directory / "policy.json").is_file() == needs_native,
                "observed calibration does not match admission")
        policy = paid_policy(directory, observation, case, summary["kernel"]) if needs_native else None
        if policy:
            register(policy["native_pid"], (program,))
        initial.setdefault(program, name)
        selections = load(directory / "selection.json")
        require(set(selections) == set(row["policies"]) == set(choices), "different policy methods")
        predictions, compared = {}, {}
        for method, (index, details) in choices.items():
            compared[method] = list(zip(prior_banks[method][:len(details)], details, strict=True))
            selected_name = name if index is None else prior_banks[method][index]
            selected = policy if index is None else trials[selected_name]["policy"]
            predicted = estimate(selected, observation)
            action = "calibrate" if index is None else "reuse"
            expected = {"schema": 1, "method": method, "action": action, "reference_index": index,
                        "policy_sha256": selected["hash"], "prediction_ns": predicted,
                        "current_sample_sha256": observation["sample_sha256"], "comparisons": details}
            require(selections[method] == expected, f"selection disagrees with profile comparisons: {name} {method}")
            compact = dict(anchor_case=selected_name, action=action, prediction_ns=predicted)
            require(row["policies"][method] == compact, "saved clock selection differs from replay")
            predictions[method] = compact
            if index is None:
                prior_banks[method].append(name)

        first = policy if initial[program] == name else trials[initial[program]]["policy"]
        predictions["always_reuse"] = {"anchor_case": initial[program], "action": "calibrate" if initial[program] == name else "reuse",
                                       "prediction_ns": estimate(first, observation)}
        selected_name = predictions["temporal"]["anchor_case"]
        active = policy if selected_name == name else trials[selected_name]["policy"]
        live = []
        for index, (slot, ms) in enumerate(zip(row["live"], case["limits_ms"], strict=True)):
            require(slot["index"] == index and slot["limit_ms"] == ms, "different live deadline schedule")
            obs = live_observation(directory / "live" / f"{index:02d}", {**slot, "kind": "limited"}, case, plan, active, reader)
            register(obs["pid"], METER)
            live.append(dict(obs, limit_ms=ms))
        plain_pid, plain_ns, plain_observation = check_plain(directory / "plain", case, reader)
        require(row["plain_pid"] == plain_pid and row["plain_cpu_ns"] == plain_ns, "plain meter CPU or PID mismatch")
        register(plain_pid, METER)
        trials[name] = dict(current, policies=predictions, compared=compared, policy=policy, native_ns=policy["native_ns"] if policy else 0,
                            full_ns=full_ns, temporal_ns=row["temporal_cpu_ns"], live=live, plain_ns=plain_ns,
                            plain_prediction_ns=estimate(active, plain_observation))

    later = sorted((c for c in cases if c["split"] == "holdout"), key=lambda c: sha(c["case_id"].encode()))
    require(len(initial) == 3 and len(later) == 10 and plan["native_rounds"] == 9, "different study population")
    samples = native_reference(root, summary, plan, later, register)
    verify_placement(root, pids)
    scored = {c["case_id"]: score_case(c, trials[c["case_id"]], samples[c["case_id"]]) for c in later}
    totals = {group: summarize([r for r in scored.values() if group == "all" or r["cohort"] == group])
              for group in ("previous", "prospective", "all")}
    return {"schema": 1, "plan_sha256": summary["plan_sha256"], "rules_sha256": summary["rules_sha256"],
            "probe_sha256": probe_sha, "summary_sha256": sha((root / "summary.json").read_bytes()),
            "cpu": 1, "steal_ticks": summary["steal_delta"], "pids_verified": len(pids),
            "native_runs": len(summary["native"]), "physical_native_calibrations": sum(bool(t["policy"]) for t in trials.values()),
            "initial_cost_ns": {p: {"full_profile": trials[n]["full_ns"], "temporal_profile": trials[n]["temporal_ns"],
                                    "native": trials[n]["native_ns"], "plain_meter": trials[n]["plain_ns"]} for p, n in initial.items()},
            "totals": totals, "cases": scored}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="extracted output directory")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    metrics = verify(args.directory)
    if args.output:
        args.output.write_text(json.dumps(metrics, indent=2) + "\n")
    print(json.dumps(metrics["totals"], indent=2))
