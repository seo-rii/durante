"""Replay the physical profiler launches and sealed Scrivener deadline decisions on one boot."""
from collections import Counter, defaultdict
from dataclasses import replace
from fractions import Fraction
from functools import cache
from math import ceil
from pathlib import Path
import re
from statistics import median
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from calibrated_limits.verify import positive, verify_placement
from contest_branch.bench import read_branch_profile
from cpp20_locality.export import parse_trace
from cpp20_locality.verify import require, sha
from first_run_sampler.profile import read_json
from scrivener_cache.bench import check_cachegrind_log, rows_digest
from scrivener_lazy import bench, corpus
from scrivener_pages.bench import check_inputs
from scrivener_streams.bench import STUDY as STREAM_STUDY

MS = 1_000_000
LANES = ("eager", "lazy")
PATH_ARMS = {"anchors": (), "scan": ("scan",), "branch": ("scan", "branch")}
BRANCH_EXEC = ("valgrind.bin", "cachegrind-amd64-linux")
SCAN_FIELDS = ("accesses", "misses", "disruptive", "position_bits")


@cache
def registered_cases(plan_raw):
    study = replace(STREAM_STUDY, corpus=corpus, controls=())
    return {c["name"]: c for c, _ in check_inputs(read_json(plan_raw), study)}


def normalized(low, high, value):
    if low == high:
        return Fraction(1, 2)
    return min(Fraction(1), max(Fraction(0), Fraction(value - low, high - low)))


def convex_interval(anchors, values):
    """Any convex reweighting of these two normalized features falls between the pure forecasts."""
    low, high = (anchors[k] for k in ("ascending", "shuffled"))
    span = high["native_ns"] - low["native_ns"]
    return sorted(ceil(low["native_ns"] + span * normalized(low[k], high[k], values[k]))
                  for k in ("disruptive", "bcm"))


def exact_bounds(anchors, values):
    """Enumerate all endpoint combinations for features not observed at this stage."""
    low, high = (anchors[k] for k in ("ascending", "shuffled"))
    endpoints = {}
    for k in ("disruptive", "bcm"):
        if low[k] == high[k]:
            endpoints[k] = [Fraction(1, 2)]
        elif k in values:
            endpoints[k] = [normalized(low[k], high[k], values[k])]
        else:
            endpoints[k] = [Fraction(0), Fraction(1)]
    estimates = [ceil(low["native_ns"] + (high["native_ns"] - low["native_ns"]) * (s + b) / 2)
                 for s in endpoints["disruptive"] for b in endpoints["bcm"]]
    return min(estimates), max(estimates)


def replay_decision(anchors, ms, values, eager):
    observed = {}
    for path, feature in (("anchors", None), ("scan", "disruptive"), ("branch", "bcm")):
        if feature:
            require(feature in values, "missing required paid feature")
            observed[feature] = values[feature]
        lower, upper = exact_bounds(anchors, observed)
        if path == "branch" or (not eager and (lower > ms * MS or upper <= ms * MS)):
            return dict(path=path, lower_ns=lower, upper_ns=upper, over_budget=lower > ms * MS)


class Journal:
    def __init__(self, root, rows, plan, cases):
        self.root, self.rows, self.plan, self.cases = root, rows, plan, cases
        self.pids = {}
        self.files = {root / f for f in ("run.json", "predictions.json", "exec-trace", "trace-stats")}

    def take(self, slot):
        seq = len(self.pids)
        require(seq < len(self.rows), "missing scheduled process")
        row = self.rows[seq]
        require(type(row["sequence"]) is int and row["sequence"] == seq
                and all(type(row[k]) is type(v) and row[k] == v for k, v in slot.items()), "wrong scheduled row")
        pid, arm, program = (row[k] for k in ("launcher_pid", "arm", "program"))
        case = self.cases[row["name"]]
        require(positive(pid) and pid not in self.pids and type(row["exit_code"]) is int and row["exit_code"] == 0
                and all(type(row[k]) is int and row[k] >= 0 for k in ("user_us", "sys_us", "cpu_us", "wall_us"))
                and row["cpu_us"] == row["user_us"] + row["sys_us"] > 0 and row["wall_us"] > 0, "invalid wait4 receipt")
        require(row["stdin_sha256"] == case["stdin_sha256"]
                and row["stdout_sha256"] == case["scan_sha256" if arm == "scan" else "stdout_sha256"]
                and row["elf_sha256"] == (self.plan["tools"]["scan"] if arm == "scan" else self.plan["binaries"][program]),
                "incorrect input, output or executable")
        directory = self.root / "runs" / f"{seq:03d}"
        if arm == "scan":
            files, chain = ("scan.out",), ("scan",)
            scan_raw = (directory / "scan.out").read_bytes()
            require(sha(scan_raw) == case["scan_sha256"], "unexpected scanner stdout")
            features = dict(zip(SCAN_FIELDS, map(int, scan_raw.split()), strict=True))
        elif arm == "branch":
            files, chain = ("cachegrind.log", "cachegrind.out"), BRANCH_EXEC
            check_cachegrind_log(directory / files[0], pid, program)
            features = {"bcm": read_branch_profile(directory / files[1], program)["Bcm"]}
        else:
            require(arm == "native" and "features" not in row, "invalid native sample")
            files, chain = (), (program,)
        if arm != "native":
            require(row["features"] == features and all(type(v) is int for v in row["features"].values()),
                    "features differ from raw profile")
        require(row["receipts"] == {f: sha((directory / f).read_bytes()) for f in files}, "raw receipt inventory or digest")
        self.files.update(directory / f for f in files)
        self.pids[pid] = chain
        return row

    def finish(self):
        require(len(self.rows) == len(self.pids), "unexpected unscheduled journal rows")
        require({p for p in self.root.rglob("*") if p.is_file()} == self.files, "unexpected file in guest archive")
        verify_placement(self.root, self.pids)
        trace = (self.root / "exec-trace").read_text()
        events, timeline = parse_trace(trace)
        measured = {"scan", *BRANCH_EXEC, *self.plan["binaries"]}
        require(len(timeline) == trace.count("sched_process_exec:") and
                {pid for pid, chain in events.items() if any(e["executable"] in measured for e in chain)} == set(self.pids),
                "unregistered measured process or malformed exec event")


def arm_costs(rows):
    amounts = defaultdict(list)
    for row in rows:
        amounts[row["arm"]].append(row["cpu_us"])
    return {k: {"calls": len(v), "child_cpu_us": sum(v)} for k, v in sorted(amounts.items())}


def anchor_features(plan, rows):
    samples = defaultdict(list)
    for r in rows:
        samples[r["name"], r["program"], r["arm"]].append(r)
    calibration = {}
    for program in plan["binaries"]:
        calibration[program] = {}
        for name in plan["anchors"]:
            scan, = samples[name, "scan", "scan"]
            branch, = samples[name, program, "branch"]
            native = [r["cpu_us"] * 1000 for r in samples[name, program, "native"]]
            require(len(native) == plan["native_reps"], "incomplete anchor")
            calibration[program][name] = dict(native_ns=median(native), disruptive=scan["features"]["disruptive"],
                                              bcm=branch["features"]["bcm"])
    return calibration


def check_call(journal, call, slot, anchors):
    require(set(call) == {*slot, "decision", "sequence_ids", "router_cpu_ns", "elapsed_ns"}
            and all(type(call[k]) is type(v) and call[k] == v for k, v in slot.items()), "wrong sealed query")
    decision = call["decision"]
    require(isinstance(decision, dict) and set(decision) == {"path", "lower_ns", "upper_ns", "over_budget"}
            and decision["path"] in PATH_ARMS and type(decision["over_budget"]) is bool
            and all(positive(decision[k]) for k in ("lower_ns", "upper_ns")), "malformed sealed decision")
    arms = PATH_ARMS[decision["path"]]
    seq = len(journal.pids)
    require(isinstance(call["sequence_ids"], list) and all(type(s) is int for s in call["sequence_ids"])
            and call["sequence_ids"] == list(range(seq, seq + len(arms))), "wrong sealed process references")
    values, costs = {}, {}
    child_wall_us = 0
    for arm in arms:
        row = journal.take(dict(lane=slot["lane"], name=slot["name"], program=slot["program"], rep=slot["rep"], arm=arm))
        values.update(row["features"])
        costs[arm] = row["cpu_us"]
        child_wall_us += row["wall_us"]
    require(positive(call["router_cpu_ns"]) and positive(call["elapsed_ns"])
            and call["elapsed_ns"] + 1000 >= max(call["router_cpu_ns"], child_wall_us * 1000), "invalid query timing")
    expected = replay_decision(anchors[slot["program"]], slot["limit_ms"], values, slot["lane"] == "eager")
    require(decision == expected, "sealed decision differs from independent bounded replay")
    return dict(**call, values=values, costs=costs)


def lane_costs(calls):
    paths = Counter(c["decision"]["path"] for c in calls)
    result = dict(calls=len(calls), paths={p: paths[p] for p in PATH_ARMS})
    for arm in ("scan", "branch"):
        samples = [c["costs"][arm] for c in calls if arm in c["costs"]]
        result.update({f"{arm}_calls": len(samples), f"{arm}_cpu_us": sum(samples)})
    result["child_cpu_us"] = result["scan_cpu_us"] + result["branch_cpu_us"]
    result["router_cpu_ns"] = sum(c["router_cpu_ns"] for c in calls)
    result["combined_cpu_ns"] = result["child_cpu_us"] * 1000 + result["router_cpu_ns"]
    result["elapsed_ns"] = sum(c["elapsed_ns"] for c in calls)
    return result


def deadline_tallies(decisions, reps):
    total = Counter(dict.fromkeys(("majority_correct", "majority_false_pass", "majority_false_timeout",
                                  "unanimous_correct", "unanimous_false_pass", "unanimous_false_timeout", "mixed"), 0))
    for predicted, votes in decisions:
        error = "correct" if predicted == (votes > reps // 2) else "false_timeout" if predicted else "false_pass"
        total["majority_" + error] += 1
        total["mixed" if 0 < votes < reps else "unanimous_" + error] += 1
    return dict(pairs_or_limits=len(decisions), **total)


def summarize(plan, calls, native_rows, calibration_rows, anchors):
    native = defaultdict(list)
    for row in native_rows:
        native[row["name"], row["program"]].append(row["cpu_us"] * 1000)
    paired = {(c["name"], c["program"], c["rep"], c["lane"]): c for c in calls}
    measured = {lane: lane_costs([c for c in calls if c["lane"] == lane]) for lane in LANES}
    eager, lazy = (measured[lane] for lane in LANES)
    for label, field in (("child", "child_cpu_us"), ("combined", "combined_cpu_ns"), ("elapsed", "elapsed_ns")):
        measured[label + "_saved_ppm"] = round(MS * Fraction(eager[field] - lazy[field], eager[field]))
    cases, fixed, grid = {}, [], []
    matches = grid_matches = 0
    grid_paths = Counter()
    errors = {"branch_stream": {}, "calibration_midpoint": {}}
    for case in (c for c in plan["cases"] if c["group"] == "holdout"):
        name, budget = case["name"], case["fixed_limit_ms"]
        cases[name] = {}
        for program in plan["binaries"]:
            native_ns = native[name, program]
            require(len(native_ns) == plan["native_reps"], "incomplete held-out native samples")
            votes = {str(ms): sum(t > ms * MS for t in native_ns) for ms in plan["fixed_limits_ms"]}
            estimates, paths = [], []
            lanes = {lane: [] for lane in LANES}
            for rep in range(1, plan["profile_reps"] + 1):
                reference, candidate = (paired[name, program, rep, lane] for lane in LANES)
                for c in (reference, candidate):
                    lanes[c["lane"]].append(c)
                values = reference["values"]
                estimate = reference["decision"]["lower_ns"]
                estimates.append(estimate)
                paths.append(candidate["decision"]["path"])
                matches += candidate["decision"]["over_budget"] == (estimate > budget * MS)
                for ms in plan["fixed_limits_ms"]:
                    decision = replay_decision(anchors[program], ms, values, False)
                    require(decision["over_budget"] == (estimate > ms * MS), "grid disagrees with eager clock")
                    grid_matches += 1
                    if rep == 1:
                        grid_paths[decision["path"]] += 1
                        grid.append((decision["over_budget"], votes[str(ms)]))
            first = lanes["lazy"][0]["decision"]
            fixed.append((first["over_budget"], votes[str(budget)]))
            midpoint = ceil(Fraction(sum(a["native_ns"] for a in anchors[program].values()), 2))
            center = median(native_ns)
            for method, prediction in (("branch_stream", estimates[0]), ("calibration_midpoint", midpoint)):
                errors[method][name, program] = abs(Fraction(prediction - center, center))
            cases[name][program] = dict(limit_ms=budget, native_ns=native_ns, median_native_ns=center, midpoint_ns=midpoint,
                                        native_tle_votes=votes, eager_estimate_ns=estimates,
                                        convex_estimate_interval_ns=convex_interval(anchors[program], lanes["eager"][0]["values"]),
                                        lazy_paths=paths,
                                        lazy_over_budget=[c["decision"]["over_budget"] for c in lanes["lazy"]],
                                        measured={lane: lane_costs(lanes[lane]) for lane in LANES})
    measured.update(equivalent_calls=matches,
                    efficiency_gate=(matches == lazy["calls"] and lazy["combined_cpu_ns"] < eager["combined_cpu_ns"]),
                    avoided_scans=eager["scan_calls"] - lazy["scan_calls"],
                    avoided_branches=eager["branch_calls"] - lazy["branch_calls"])
    summary = dict(measured=measured, calibration=arm_costs(calibration_rows), validation=arm_costs(native_rows),
                   fixed=deadline_tallies(fixed, plan["native_reps"]), grid=deadline_tallies(grid, plan["native_reps"]),
                   grid_eager_comparisons=grid_matches, grid_paths={p: grid_paths[p] for p in PATH_ARMS})
    accuracy = {}
    for method, values in errors.items():
        worst = max(values, key=values.get)
        accuracy[method] = dict(cases=len(values), within_20pct=sum(e <= Fraction(1, 5) for e in values.values()),
                                median_absolute_error_ppm=round(MS * median(values.values())),
                                worst_absolute_error_ppm=round(MS * values[worst]), worst_case=list(worst))
    summary["descriptive_accuracy"] = accuracy
    return summary, cases


def verify(root):
    require(not any(p.is_symlink() for p in root.rglob("*")), "symlink in guest archive")
    plan_raw = (HERE / "plan.json").read_bytes()
    plan = read_json(plan_raw)
    run_raw = (root / "run.json").read_bytes()
    seal_raw = (root / "predictions.json").read_bytes()
    run, seal = map(read_json, (run_raw, seal_raw))
    plan_sha = sha(plan_raw)
    require(type(run["schema"]) is int and run["schema"] == plan["schema"] == 1
            and run["authority"] == plan["authority"] == "diagnostic_only" and run["plan_sha256"] == plan_sha
            and run["tools"] == plan["tools"] and set(plan["binaries"]) == {"scrivener", "scrivener_lifting"}
            and (plan["profile_reps"], plan["native_reps"]) == (2, 9) and plan["fixed_limits_ms"] == list(range(30, 90, 5)),
            "wrong registration, tools, limits or repetitions")
    host = run["placement"]
    require(type(host["cpu"]) is int and host["cpu"] == plan["cpu"] == 1 and run["cgroup_allowed_cpus"] == "1"
            and host["machine"] == "x86_64" and re.fullmatch(r"[0-9a-f]{64}", host["boot_sha256"])
            and isinstance(host["kernel"], str) and host["kernel"] and type(run["cpu_steal_ticks"]) is int
            and run["cpu_steal_ticks"] >= 0, "invalid guest placement")
    require(set(seal) == {"plan_sha256", "boot_sha256", "prefix_sha256", "calls"} and run["predictions_sha256"] == sha(seal_raw)
            and seal["plan_sha256"] == plan_sha and seal["boot_sha256"] == host["boot_sha256"], "invalid pre-label seal")
    rows = run["rows"]
    journal = Journal(root, rows, plan, registered_cases(plan_raw))
    calibration = [journal.take(slot) for slot in bench.calibration_schedule(plan)]
    anchors = anchor_features(plan, calibration)
    calls = [check_call(journal, call, slot, anchors)
             for call, slot in zip(seal["calls"], bench.calls_schedule(plan), strict=True)]
    cutoff = len(journal.pids)
    require(seal["prefix_sha256"] == rows_digest(rows[:cutoff]), "sealed prefix digest differs from physical queries")
    validation = [journal.take(slot) for slot in bench.validation_schedule(plan)]
    journal.finish()
    summary, cases = summarize(plan, calls, validation, calibration, anchors)
    return dict(schema=1, authority="diagnostic_only", plan_sha256=plan_sha, run_sha256=sha(run_raw),
                predictions_sha256=sha(seal_raw), boot_sha256=host["boot_sha256"], cpu_steal_ticks=run["cpu_steal_ticks"],
                verified_pids=len(journal.pids), sealed_prefix_rows=cutoff, anchors=anchors, summary=summary, cases=cases)


if __name__ == "__main__":
    from scrivener_pages.verify import main
    main(verify)
