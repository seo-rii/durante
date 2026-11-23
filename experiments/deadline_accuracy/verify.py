"""Reconstruct paired TLE decisions from paid calibration, raw v11 counters, exec traces and held-out wait4 CPU."""
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
from calibrated_limits.verify import METER, TYPES, live_observation, load, positive, verify_placement
from cpp20_locality.export import parse_trace
from cpp20_locality.verify import release_reader, require, sha
from first_run_sampler.profile import digest
from footprint_admission.verify import select
from guarded_native import anchor
from guarded_native.score import MS, boundary
from shape_calibration import combined, temporal
from shape_calibration import verify as prior

PACKET_ESTIMATES = {"gate": "packet_median", "exact": "packet_exact"}


def exact(actual, expected, message):
    require(json.dumps(actual, sort_keys=True) == json.dumps(expected, sort_keys=True), message)


def thresholds(prediction, percents, fixed):
    adaptive = defaultdict(list)
    for percent in percents:
        ms = max(1, ceil(Fraction(prediction * percent, 100 * MS)))
        adaptive[ms].append(percent)
    return [(ms, ms in fixed, adaptive[ms]) for ms in sorted(set(fixed) | adaptive.keys())]


def baseline(cases_root, case, row, profiles, bank, clocks, kernel, register, boot):
    key = case["case_id"]
    path = cases_root / key
    full = prior.check_full(case, path / "full")
    obs = full["observation"]
    order = combined.inspect_temporal(path, case["binary_sha256"], sha(case["stdin"].encode()),
                                      sha(case["stdout"].encode()), temporal.PROBE_SHA)
    cpu = prior.profile_cpu_ns(path / "full")
    exact([row["profile_pid"], row["profile_cpu_ns"]], [obs["pid"], cpu], "wrong profile PID or CPU")
    require(order["observation"]["cpu_ns"] == cpu, "different combined CPU charge")
    register(obs["pid"], METER)
    current = (full, order)
    index, comparisons = select("footprint", bank, profiles, current)
    profiles[key] = current
    paid = index is None
    require(paid == (path / "policy.json").exists(), "unexpected native payment")
    if paid:
        clocks[key] = prior.paid_policy(path, obs, case, kernel)
        native = load(path / "policy.json")["native"]
        require(digest(native["boot_sha256"]), "missing paid boot identity")
        boot.add(native["boot_sha256"])
        require(len(boot) == 1, "multiple paid guest boots")
        register(clocks[key]["native_pid"], (case["program"],))
        bank.append(key)
    selected = key if paid else bank[index]
    clock = clocks[selected]
    estimate = prior.estimate(clock, obs)
    exact([row["anchor_case"], row["baseline_ns"]], [selected, estimate], "wrong baseline anchor or estimate")
    selection = dict(schema=1, method="footprint", action="calibrate" if paid else "reuse", reference_index=index,
                     policy_sha256=clock["hash"], prediction_ns=estimate, comparisons=comparisons,
                     current_sample_sha256=obs["sample_sha256"])
    selection_file = path / "baseline-selection.json"
    exact(load(selection_file), selection, "wrong chronological footprint selection")
    return obs, (selection_file, cases_root / selected / "policy.json", clock), dict(
        anchor_case=selected, baseline_ns=estimate, profile_cpu_ns=cpu,
        baseline_paid_cpu_ns=clock["native_ns"] if paid else 0, extra_ns=[], deadlines=[])


def read_packet(directory, source, obs, case, kernel, boot, register, candidate):
    selection_path, policy_path, baseline_clock = source
    context = dict(cpu=1, kernel=kernel, machine="x86_64", boot_sha256=boot,
                   source_selection_sha256=sha(selection_path.read_bytes()), source_policy_sha256=baseline_clock["hash"],
                   elf_sha256=obs["elf_sha256"], stdin_sha256=obs["stdin_sha256"], current_result_sha256=obs["result_sha256"])
    raw_source = policy_path.read_bytes()
    # Every baseline payment in this collection was made on the registered guest.
    reuse = load(policy_path)["anchor"] == obs
    clocks, paths, extra = [], [], []
    count = 1 if candidate == "exact" else 3
    expected = {"selection.json", "packet.json"} | {f"policy-{i}.json" for i in range(count)}
    require({path.name for path in directory.iterdir()} == expected, "unexpected packet sample or file")
    for i in range(count):
        path = directory / f"policy-{i}.json"
        raw = path.read_bytes()
        data = load(path)
        parsed = anchor.clock_from_raw(raw, obs["elf_sha256"])
        exact(data["anchor"], obs, "packet belongs to another complete capture")
        n = data["native"]
        cpu = prior.check_native(n, case, kernel)
        require(n.get("boot_sha256") == boot and cpu == parsed.native_ns, "different packet native placement")
        if i == 0 and reuse:
            require(raw == raw_source, "packet did not reuse the original current payment")
        else:
            register(n["pid"], (case["program"],))
            extra.append(cpu)
        clocks.append(dict(hash=sha(raw), weights=parsed.weights_ns, work=parsed.calibration_work, native_ns=cpu))
        paths.append(path)
    hashes = [c["hash"] for c in clocks]
    manifest = dict(authority="diagnostic_only", context=context, reused_original=reuse, policy_sha256=hashes)
    if candidate == "exact":
        selected_index = 0
        manifest.update(schema=2, strategy="exact-one", selected_index=selected_index)
    else:
        selected_index = sorted(range(count), key=lambda i: clocks[i]["native_ns"])[1]
        manifest.update(schema=1, median_index=selected_index)
    exact(load(directory / "packet.json"), manifest, f"invalid {'first-exact' if candidate == 'exact' else 'median'} packet or source binding")
    selected = directory / "selection.json"
    choice = dict(schema=1, method="footprint", action="calibrate", reference_index=None, comparisons=[],
                  policy_sha256=hashes[selected_index], prediction_ns=clocks[selected_index]["native_ns"], current_sample_sha256=obs["sample_sha256"])
    exact(load(selected), choice, "wrong packet selection")
    return (selected, paths[selected_index], clocks[selected_index]), extra


def live(root, dest, event, binding, obs, case, plan, reader, register):
    selection_file, policy_file, clock = binding
    exact([event["selection_path"], event["policy_path"]], [str(p.relative_to(root)) for p in (selection_file, policy_file)],
          "recorded enforcement belongs to a different policy")
    selected = load(selection_file)
    raw = load(dest / "live/result.json")
    wrapper = dict(**raw, selection_sha256=sha(selection_file.read_bytes()), method="footprint",
                   action=selected["action"], reference_index=selected["reference_index"],
                   current_sample_sha256=obs["sample_sha256"], current_result_sha256=obs["result_sha256"],
                   preflight_prediction_ns=selected["prediction_ns"])
    exact(load(dest / "result.json"), wrapper, "wrapped decision differs from the capture or limit")
    entry = live_observation(dest / "live", {**event, "kind": "limited"}, case, plan, clock, reader)
    register(entry["pid"], METER)
    return {**{k: entry[k] for k in ("pid", "tle", "observation", "prediction_ns", "wall_ns")},
            "preflight_ns": selected["prediction_ns"]}


def replay_limits(root, seq, case, row, source, obs, trial, plan, kernel, boot, reader, register, candidate):
    arms = ("baseline", candidate)
    folder = root / "cases" / case["case_id"]
    directory = folder / "deadline-packet"
    packet = None
    events = iter(row["limits"])
    registered = thresholds(trial["baseline_ns"], plan["deadline_percent"], case["fixed_limits_ms"])
    for i, (ms, fixed, adaptive) in enumerate(registered if case["split"] == "holdout" else []):
        near = 4 * abs(trial["baseline_ns"] - ms * MS) <= ms * MS
        use_packet = near or candidate == "exact"
        if use_packet and packet is None:
            packet, trial["extra_ns"] = read_packet(directory, source, obs, case, kernel, boot, register, candidate)
            trial[PACKET_ESTIMATES[candidate] + "_ns"] = packet[2]["native_ns"]
        point = dict(limit_ms=ms, near=near, fixed=fixed, adaptive_percent=adaptive)
        for arm in (arms if (seq+i) % 2 == 0 else arms[::-1]):
            event = next(events, None)
            require(isinstance(event, dict), "missing live event")
            exact([event[k] for k in ("index", "limit_ms", "method", "near")], [i,ms,arm,near],
                  "changed deadline, gate boundary or execution order")
            choice = packet if use_packet and arm == candidate else source
            point[arm] = live(root, folder / "limits" / arm / f"{i:02d}", event, choice, obs, case, plan, reader, register)
        trial["deadlines"].append(point)
    require(next(events, None) is None and directory.exists() == (packet is not None), "unexpected extra run or payment")


def annotate(trial, samples, candidate="gate"):
    center = median(samples)
    trial.update(native_ns=samples, median_ns=center, min_ns=min(samples), max_ns=max(samples))
    for key in ("baseline_ns", PACKET_ESTIMATES[candidate] + "_ns"):
        if key in trial:
            diff = trial[key] - center
            trial[key.removesuffix("_ns") + "_signed_error_ppm"] = round(Fraction(diff, center) * MS)
            trial[key.removesuffix("_ns") + "_within_20pct"] = abs(diff) * 5 <= center
    for point in trial["deadlines"]:
        ms = point["limit_ms"]
        votes = sum(t > ms * MS for t in samples)
        point.update(native_tle_reps=votes, native_majority_tle=votes * 2 > len(samples),
                     native_median_margin_ns=center - ms * MS)
        for arm in ("baseline", candidate):
            entry = point[arm]
            outcome = boundary(samples, [entry["prediction_ns"]], ms)
            entry["native_class"] = outcome["classification"]
            entry["majority_match"] = entry["tle"] == point["native_majority_tle"]
            entry["preflight_match"] = entry["tle"] == (entry["preflight_ns"] > ms * MS)


def summarize(points, candidate="gate"):
    counts = dict(limits=len(points), near=sum(p["near"] for p in points),
                  fixed_and_adaptive=sum(bool(p["fixed"] and p["adaptive_percent"]) for p in points),
                  changed=sum(p["baseline"]["tle"] != p[candidate]["tle"] for p in points))
    for arm in ("baseline", candidate):
        decisions = [p[arm] for p in points]
        classes = Counter(e["native_class"] for e in decisions)
        counts[arm] = {**{k: classes[k] for k in TYPES},
                       "majority_matches": sum(e["majority_match"] for e in decisions),
                       "live_kills": sum(e["observation"] == "live" for e in decisions),
                       "preflight_disagreements": sum(not e["preflight_match"] for e in decisions)}
    return counts


def aggregate(cases, candidate):
    points = [p for case in cases for p in case["deadlines"]]
    result = dict(inputs=len(cases), profile_cpu_ns=sum(c["profile_cpu_ns"] for c in cases),
                  baseline_paid_cpu_ns=sum(c["baseline_paid_cpu_ns"] for c in cases),
                  baseline_paid_runs=sum(c["baseline_paid_cpu_ns"] > 0 for c in cases))
    result[candidate + "_extra_cpu_ns"] = sum(sum(c["extra_ns"]) for c in cases)
    result[candidate + "_extra_runs"] = sum(len(c["extra_ns"]) for c in cases)
    for name in ("baseline", PACKET_ESTIMATES[candidate]):
        scores = [c for c in cases if name+"_signed_error_ppm" in c]
        errors = [abs(c[name+"_signed_error_ppm"]) for c in scores]
        result[name+"_estimates"] = dict(cases=len(scores), within_20pct=sum(c[name+"_within_20pct"] for c in scores),
                                         median_absolute_error_ppm=median(errors) if errors else None,
                                         max_absolute_error_ppm=max(errors, default=None))
    result["deadlines"] = {label:summarize(group, candidate) for label, group in (
        ("all", points), ("fixed", [p for p in points if p["fixed"]]), ("adaptive", [p for p in points if p["adaptive_percent"]]),
        ("overlap", [p for p in points if p["fixed"] and p["adaptive_percent"]]),
        ("near", [p for p in points if p["near"]]), ("far", [p for p in points if not p["near"]]))}
    # Independently enforced trials can disagree across limits if the counter stream changes between processes.
    result["monotonicity_violations"] = {
        arm:sum(not a[arm]["tle"] and b[arm]["tle"]
                for c in cases for a,b in zip(c["deadlines"], c["deadlines"][1:])) for arm in ("baseline", candidate)}
    return result


def verify(root, strategy="near-three"):
    require(strategy in ("near-three", "exact-one"), "unknown registered strategy")
    candidate = "exact" if strategy == "exact-one" else "gate"
    prefix = "exact-" if candidate == "exact" else ""
    plan_path, rules_path = (HERE / f"{prefix}{name}.json" for name in ("plan", "rules"))
    plan, rules, summary = (load(p) for p in (plan_path, rules_path, root / "summary.json"))
    require(summary["plan_sha256"] == sha(plan_path.read_bytes())
            and summary["rules_sha256"] == sha(rules_path.read_bytes())
            and summary["probe_sha256"] == temporal.PROBE_SHA == rules["probe_sha256"] == plan["probe_sha256"]
            and summary["schema"] == rules["schema"] == plan["schema"] == 1 and summary["affinity"] == [1]
            and summary["cgroup_cpus"] == "1" and isinstance(summary["kernel"], str) and summary["kernel"]
            and type(summary["steal_delta"]) is int and summary["steal_delta"] >= 0, "different registration or guest")
    reader = release_reader(load(anchor.PROTOCOL)["tools_sha256"]["production_reader.py"])
    pids = {}
    def register(pid, commands):
        require(positive(pid) and pid not in pids, "duplicate physical workload PID")
        pids[pid] = commands
    profiles, banks, clocks, trials, boot = {}, defaultdict(list), {}, {}, set()
    for seq, (case, row) in enumerate(zip(plan["cases"], summary["rows"], strict=True)):
        key, program = case["case_id"], case["program"]
        exact([row["sequence"],row["case_id"]], [seq,key], "case order mismatch")
        require(key not in trials and (case["split"] == "calibration") == (program not in banks), "unexpected case or seed")
        inp = case["stdin"].encode()
        require((root / "inputs" / sha(inp)).read_bytes() == inp, "different input supplied")
        obs, source, trial = baseline(root / "cases", case, row, profiles, banks[program], clocks,
                                      summary["kernel"], register, boot)
        replay_limits(root, seq, case, row, source, obs, trial, plan, summary["kernel"], next(iter(boot)), reader, register, candidate)
        trials[key] = dict(**trial, cohort=case["cohort"], program=program)
    held = sorted((c for c in plan["cases"] if c["split"] == "holdout"), key=lambda c:sha(c["case_id"].encode()))
    independent = prior.native_reference(root, summary, plan, held, register)
    verify_placement(root, pids)
    trace, _ = parse_trace((root / "exec-trace").read_text())
    workload_names = set(METER) | banks.keys()
    actual = {pid for pid, events in trace.items() if any(e["executable"] in workload_names for e in events)}
    require(actual == set(pids), "unaccounted measured process in the trace")
    for case in held:
        key = case["case_id"]
        annotate(trials[key], independent[key], candidate)
    held_trials = [trials[c["case_id"]] for c in held]
    seeds = {k:v for k,v in trials.items() if v["cohort"] == "calibration"}
    return dict(schema=1, plan_sha256=summary["plan_sha256"], rules_sha256=summary["rules_sha256"],
                summary_sha256=sha((root / "summary.json").read_bytes()), verified_pids=len(pids),
                native_runs=len(summary["native"]), steal_ticks=summary["steal_delta"], seeds=seeds,
                totals={name:aggregate([c for c in held_trials if name == "all" or c["cohort"] == name], candidate)
                        for name in ("all","prospective","previous")},
                cases={c["case_id"]:trials[c["case_id"]] for c in held})


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("directory", type=Path)
    cli.add_argument("--output", type=Path)
    cli.add_argument("--strategy", choices=("near-three", "exact-one"), default="near-three")
    args = cli.parse_args()
    metrics = verify(args.directory, args.strategy)
    if args.output:
        args.output.write_text(json.dumps(metrics, indent=2) + "\n")
    print(json.dumps(metrics["totals"], indent=2))
