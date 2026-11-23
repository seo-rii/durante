"""Replay one pinned-CPU calibrated deadline study and compare actual stops to independent native runs."""
import argparse
from collections import Counter, defaultdict
from fractions import Fraction
import json
from math import ceil
from pathlib import Path
import re
import signal
from statistics import median
import struct
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from cpp20_locality.export import parse_trace
from cpp20_locality.verify import release_reader, require, sha, verify_counters
from first_run_sampler.profile import read_json
from guarded_native import anchor
from guarded_native.score import boundary, MS

CLOCK = struct.Struct("<6Q")
METER = ("drrun", "libdynamorio.so")
TYPES = ("agreement", "false_tle", "false_accept", "mixed_native")


def load(path):
    return read_json(path.read_bytes())


def positive(value):
    return type(value) is int and value > 0


def wait4_ns(row):
    require(all(type(row[k]) is int and row[k] >= 0 for k in ("user_us", "sys_us", "cpu_ns"))
            and row["cpu_ns"] == (row["user_us"] + row["sys_us"]) * 1000 > 0, "invalid native wait4 time")
    return row["cpu_ns"]


def policy_from_receipts(root, case):
    program = case["program"]
    path = root / "policies" / (program + ".json")
    raw = path.read_bytes()
    saved = read_json(raw)
    report = anchor.measured(root / "first" / program, case["binary_sha256"])
    native = saved["native"]
    require(saved["schema"] == 1 and saved["authority"] == "diagnostic_only"
            and saved["reference_sha256"] == anchor.REFERENCE_SHA and saved["combined_reference_weights"] == anchor.weights(),
            "wrong clock registration for " + program)
    require(report == saved["anchor"] and report["mode"] == "full" and report["launch_count"] == 1
            and report["stdin_sha256"] == sha(case["stdin"].encode())
            and report["stdout_sha256"] == native["stdout_sha256"] == sha(case["stdout"].encode())
            and saved["calibration_sha256"] == anchor.calibration_digest(report, native), "anchor receipt mismatch: " + program)
    require(positive(native["pid"]) and native["affinity"] == [1] and native["single_cpu"] is True
            and native["machine"] == "x86_64" and isinstance(native["kernel"], str) and native["kernel"],
            "wrong native calibration placement: " + program)
    # An independent rational implementation of the registered first-input ratio.
    weights = {k: Fraction(v) for k, v in saved["combined_reference_weights"].items()}
    calibration_work = sum(weights[k] * report[k] for k in weights)
    require(calibration_work > 0, "empty calibration work")
    return {"hash": sha(raw), "native_ns": wait4_ns(native), "weights": weights, "work": calibration_work,
            "meter_pid": report["pid"], "native_pid": native["pid"]}


def clock_words(path):
    raw = path.read_bytes()
    require(len(raw) == CLOCK.size, "invalid live clock length")
    words = CLOCK.unpack(raw)
    require(words[:2] == (1, 0) and words[-1] == 0, "uninitialized, invalidated or overflowing clock")
    return words


def live_observation(directory, slot, case, plan, policy, reader):
    r = load(directory / "result.json")
    threshold = slot["limit_ms"] * MS
    require(r["schema"] == 1 and r["authority"] == "diagnostic_only" and r["limit_ms"] == slot["limit_ms"]
            and r["poll_ms"] == plan["poll_ms"] and r["timeout_ms"] == plan["timeout_ms"]
            and r["policy_sha256"] == policy["hash"] and r["elf_sha256"] == case["binary_sha256"]
            and r["reference_sha256"] == anchor.REFERENCE_SHA and r["anchor_native_ns"] == policy["native_ns"],
            "wrong live clock identity or limit")
    require((directory / "stdin").read_bytes() == case["stdin"].encode()
            and r["stdin_sha256"] == sha(case["stdin"].encode()) and (directory / "stderr").read_bytes() == b"",
            "wrong live input or stderr")
    stdout = (directory / "stdout").read_bytes()
    require(r["stdout_sha256"] == sha(stdout), "wrong live stdout digest")
    pid = r["pid"]
    require(positive(pid) and positive(r["wall_ns"]) and type(r["polls"]) is int and r["polls"] >= 0,
            "missing live process identity or polling time")
    names = set((directory / "records").iterdir())
    record = directory / "records" / f"process-{pid}.jsonl"
    mode = r["observation"]
    require(mode in ("live", "terminal"), "a live run did not return a work-clock decision")
    terminal_clock = clock_words(directory / "clock.bin")
    terminal_event_at_kill = False
    if mode == "live":
        words = clock_words(directory / "crossing.bin")
        require(all(a <= b for a, b in zip(words, terminal_clock)) and words[2] > 0 and r["polls"] > 0
                and r["returncode"] == -signal.SIGKILL and r["status"] == "budget_exceeded"
                and not (directory / "production.json").exists() and names == {record}, "invalid live crossing evidence")
        with record.open("rb") as stream:
            start = read_json(stream.readline())
            require(start.get("event") == "start" and start.get("seq") == 1 and start.get("pid") == pid
                    and start.get("origin") == "initial" and start.get("cpuid_profile") == "x86-64-v2"
                    and start.get("authority") == "diagnostic_only" and start.get("rep_bytes_enabled") is True
                    and start.get("rep_breakdown_version") == 1, "invalid v11 prefix at stop")
            for line in stream:
                if line.endswith(b"\n") and read_json(line).get("event") == "end":
                    terminal_event_at_kill = True
    else:
        words = terminal_clock
        require(not (directory / "crossing.bin").exists() and names == {record} and r["returncode"] == 0
                and stdout == case["stdout"].encode(), "wrong terminal process, output or evidence")
        prod = load(directory / "production.json")
        verify_counters(record.read_bytes(), prod, CLOCK.pack(*words), "live", pid, 3 * (2**64 - 1), reader)
    progress = {"instructions": words[2], "movs_bytes": words[3], "stos_bytes": words[4],
                "rep_bytes": words[3] + words[4]}
    cost = sum(w * progress[k] for k, w in policy["weights"].items())
    prediction = ceil(cost * policy["native_ns"] / policy["work"])
    tle = prediction > threshold
    require(r["progress"] == progress and r["prediction_ns"] == prediction and type(r["prediction_ns"]) is int
            and r["status"] == ("budget_exceeded" if tle else "within_budget")
            and slot["exit_code"] == (123 if tle else 0), "clock formula, status or CLI exit mismatch")
    require(slot["kind"] != "control" or (mode == "terminal" and not tle), "control failed to reach completion")
    return {"pid": pid, "instructions": words[2], "rep_bytes": words[3] + words[4],
            "prediction_ns": prediction, "tle": tle, "observation": mode, "polls": r["polls"],
            "wall_ns": r["wall_ns"], "end_event_at_kill": terminal_event_at_kill, "oracle_output_present": stdout == case["stdout"].encode()}


def verify_placement(root, expected):
    events, timeline = parse_trace((root / "exec-trace").read_text())
    # The raw instance trace includes the controller, wrappers and unrelated guest activity.
    observed_order = [pid for pid, _ in timeline if pid in expected]
    require(observed_order == [pid for pid, commands in expected.items() for _ in commands],
            "benchmark exec trace order differs from the collection schedule")
    for pid, commands in expected.items():
        require(events.get(pid) == [{"cpu": 1, "executable": name} for name in commands],
                "unexpected executable or CPU for PID " + str(pid))
    stats = (root / "trace-stats").read_text()
    for field in ("overrun", "commit overrun", "dropped events"):
        counts = re.findall("^" + field + r":\s*(\d+)", stats, re.M)
        require(len(counts) >= 2 and all(int(n) == 0 for n in counts), "lossy or missing trace statistic: " + field)


def lookup_scale_bound(controls, samples, weights):
    """A post-hoc bound for any common nonnegative scale on the observed within-ELF control work."""
    long, shuffled = "lookup/long", "lookup/shuffled"
    counts = {name: {k: controls[name][k] for k in weights} for name in (long, shuffled)}
    work = {name: sum(weights[k] * v for k, v in values.items()) for name, values in counts.items()}
    ratio = work[long] / work[shuffled]
    low = min(samples[shuffled])
    high = max(samples[long])
    require(ratio > 1, "expected more registered work on lookup/long")
    # If P_i = ceil(s * W_i), then P_long >= ratio * P_shuffled - ratio nanoseconds.
    # Give the model the fastest shuffled and slowest long native times.
    error = max(Fraction(0), (ratio * low - high - ratio) / (ratio * low + high))
    return {"scope": "post_hoc_common_scale_on_frozen_work_at_observed_control_counts",
            "numerator_case": long, "denominator_case": shuffled, "control_counts": counts,
            "weights": {k: str(v) for k, v in weights.items()}, "work_ratio": str(ratio),
            "denominator_min_native_ns": low, "numerator_max_native_ns": high,
            "max_native_ratio": str(Fraction(high, low)), "relative_error_lower_bound": str(error),
            "excludes_20pct": 5 * error > 1}


def verify(root):
    raw_plan = (HERE / "plan.json").read_bytes()
    plan = read_json(raw_plan)
    summary = load(root / "summary.json")
    require(plan["schema"] == summary["schema"] == 1 and summary["plan_sha256"] == sha(raw_plan)
            and summary["affinity"] == [1] and summary["cgroup_cpus"] == "1"
            and type(summary["steal_delta"]) is int and summary["steal_delta"] >= 0, "wrong collection identity")
    first = [c for c in plan["cases"] if c["split"] == "calibration"]
    later = sorted((c for c in plan["cases"] if c["split"] == "holdout"), key=lambda c: sha(c["case_id"].encode()))
    records = {}
    for c in plan["cases"]:
        require((root / "inputs" / sha(c["stdin"].encode())).read_bytes() == c["stdin"].encode(),
                "archived input differs for " + c["case_id"])
        require(c["case_id"] not in records, "duplicate registered input")
        records[c["case_id"]] = c
    require(len(records) == len(first) + len(later) and len(first) == 7 and len(later) == 12
            and plan["native_rounds"] == 9 and sum(len(c["limits_ms"]) for c in later) == 32, "different study population")
    require(summary["calibrations"] == [{k: c[k] for k in ("case_id", "program")} for c in first],
            "different calibration sequence")
    policies = {c["program"]: policy_from_receipts(root, c) for c in first}
    pids = {}

    def register(pid, commands):
        require(positive(pid) and pid not in pids, "duplicate/missing measured PID")
        pids[pid] = commands

    for program, pol in policies.items():
        register(pol["meter_pid"], METER)
        register(pol["native_pid"], (program,))

    samples = defaultdict(list)
    schedule = [(r + 1, c) for r in range(plan["native_rounds"]) for c in later[r:] + later[:r]]
    for seq, (row, (round_id, case)) in enumerate(zip(summary["native"], schedule, strict=True)):
        name = case["case_id"]
        require(row["round"] == round_id and row["case_id"] == name and row["sequence"] == seq, "different native rotation")
        register(row["pid"], (case["program"],))
        directory = root / "native" / f"{seq:03d}"
        require((directory / "stdout").read_bytes() == case["stdout"].encode() and (directory / "stderr").read_bytes() == b""
                and row["stdout_sha256"] == sha(case["stdout"].encode()), "wrong direct native output: " + name)
        samples[name].append(wait4_ns(row))

    reader_hash = load(anchor.PROTOCOL)["tools_sha256"]["production_reader.py"]
    reader = release_reader(reader_hash)
    jobs = [(c["case_id"], "control", plan["control_limit_ms"]) for c in later]
    jobs += [(c["case_id"], "limited", ms) for c in later for ms in c["limits_ms"]]
    jobs.sort(key=lambda j: sha(f"{j[0]} {j[1]} {j[2]}".encode()))
    controls = {}
    limits = defaultdict(dict)
    for seq, (slot, (name, kind, ms)) in enumerate(zip(summary["runs"], jobs, strict=True)):
        require(slot["sequence"] == seq and slot["case_id"] == name and slot["kind"] == kind and slot["limit_ms"] == ms,
                "different live job schedule or deadline")
        case = records[name]
        obs = live_observation(root / "live" / f"{seq:03d}", slot, case, plan, policies[case["program"]], reader)
        register(obs["pid"], METER)
        if kind == "control":
            controls[name] = obs
        else:
            limits[name][ms] = obs
    verify_placement(root, pids)

    results = {}
    for case in later:
        name = case["case_id"]
        control = controls[name]
        exact = control["prediction_ns"]
        native = samples[name]
        center = median(native)
        decisions = []
        for ms in case["limits_ms"]:
            obs = limits[name][ms]
            verdict = boundary(native, [obs["prediction_ns"]], ms)
            over = obs["tle"]
            decisions.append({**obs, "limit_ms": ms, "native_exceeded": verdict["native_tle_reps"],
                              "native_class": verdict["classification"], "control_exceeded": exact > ms * MS,
                              "agrees_with_control": over == (exact > ms * MS),
                              "over_limit_ns": max(0, obs["prediction_ns"] - ms * MS)})
        results[name] = {"cohort": case["cohort"], "native_ns": native, "min_ns": min(native),
                         "median_ns": center, "max_ns": max(native),
                         "control_prediction_ns": exact, "control_wall_ns": control["wall_ns"],
                         "signed_error_ppm": round(Fraction(exact - center, center) * MS),
                         "within_20pct": 5 * abs(exact - center) <= center, "limits": decisions}

    totals = {}
    for group in ("previous", "new", "all"):
        cases = [c for c in results.values() if group == "all" or c["cohort"] == group]
        trials = [d for c in cases for d in c["limits"]]
        tally = Counter(d["native_class"] for d in trials)
        stops = [d for d in trials if d["observation"] == "live"]
        totals[group] = {"inputs": len(cases), "within_20pct": sum(c["within_20pct"] for c in cases),
                         "limits": len(trials), **{k: tally[k] for k in TYPES},
                         "control_disagreements": sum(not d["agrees_with_control"] for d in trials),
                         "live_kills": len(stops), "live_kills_with_end_event": sum(d["end_event_at_kill"] for d in stops),
                         "terminal_exceeded": sum(d["observation"] == "terminal" and d["tle"] for d in trials),
                         "completed_within": sum(not d["tle"] for d in trials),
                         "max_live_overshoot_ns": max((d["over_limit_ns"] for d in stops), default=None)}
    return {"schema": 1, "plan_sha256": sha(raw_plan), "summary_sha256": sha((root / "summary.json").read_bytes()),
            "cpu": 1, "steal_ticks": summary["steal_delta"], "pids_verified": len(pids), "native_runs": len(schedule),
            "anchor_native_ns": {p: v["native_ns"] for p, v in policies.items()},
            "within_elf_scale_bound": lookup_scale_bound(controls, samples, policies["lookup"]["weights"]),
            "totals": totals, "cases": results}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="extracted output directory with raw receipts")
    parser.add_argument("--output", type=Path, help="save the recomputed JSON metrics")
    args = parser.parse_args()
    metrics = verify(args.directory)
    if args.output:
        args.output.write_text(json.dumps(metrics, indent=2) + "\n")
    print(json.dumps(metrics["totals"], indent=2))
