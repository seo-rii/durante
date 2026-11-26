"""Replay the raw profiles, sealed held-out forecasts and independent CPU timings."""
import argparse
from fractions import Fraction
from functools import cache
import json
from pathlib import Path
import re
from statistics import median
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from calibrated_limits.verify import METER, positive, verify_placement
from cpp20_locality.export import parse_trace
from cpp20_locality.verify import release_reader, require, sha
from first_run_sampler.profile import read_json
from scrivener_cache import bench, corpus, score
from scrivener_order.bench import read_profile

ENDPOINTS = ("ascending", "shuffled")


@cache
def features(plan_raw):
    values = {}
    for row in read_json(plan_raw)["cases"]:
        text, output = corpus.make(row["name"])
        require(sha(text.encode()) == row["stdin_sha256"] and sha(output.encode()) == row["stdout_sha256"],
                "input or oracle differs from registration")
        values[row["name"]] = corpus.position_bits(text)
    return values


def verify(root):
    raw = (HERE / "plan.json").read_bytes()
    plan = read_json(raw)
    plan_sha = sha(raw)
    run_raw = (root / "run.json").read_bytes()
    run = read_json(run_raw)
    require(run["schema"] == plan["schema"] == 1 and run["authority"] == plan["authority"] == "diagnostic_only"
            and run["plan_sha256"] == plan_sha and run["tools"] == plan["tools"], "wrong registration or tools")
    require(plan["cache_geometry"] == {k: [v, 8, 64] for k, v in bench.CACHES.items()},
            "registered geometry differs from the collector")
    place = run["placement"]
    require(type(place["cpu"]) is int and place["cpu"] == 1 and run["cgroup_allowed_cpus"] == "1"
            and place["machine"] == "x86_64" and re.fullmatch(r"[0-9a-f]{64}", place["boot_sha256"])
            and bool(place["kernel"]) and type(run["cpu_steal_ticks"]) is int and run["cpu_steal_ticks"] >= 0,
            "invalid placement")
    reader = release_reader(plan["tools"]["reader"])
    registered = {r["name"]: r for r in plan["cases"]}
    bits = features(raw)
    cases = {n: {p: dict(position_bits=bits[n], native_ns=[]) for p in plan["binaries"]} for n in registered}
    rows = run["rows"]
    cutoff = None
    files, pids = set(), {}
    for seq, (row, slot) in enumerate(zip(rows, bench.schedule(plan), strict=True)):
        require(type(row["sequence"]) is int and row["sequence"] == seq
                and all(type(row[k]) is type(v) and row[k] == v for k, v in slot.items()), "wrong schedule")
        name, program, arm = slot["name"], slot["program"], slot["arm"]
        source, case = registered[name], cases[name][program]
        pid = row["launcher_pid"]
        require(row["elf_sha256"] == plan["binaries"][program] and row["stdin_sha256"] == source["stdin_sha256"]
                and row["stdout_sha256"] == source["stdout_sha256"], "wrong ELF, input or answer")
        require(positive(pid) and pid not in pids and type(row["exit_code"]) is int and row["exit_code"] == 0
                and all(type(row[k]) is int and row[k] >= 0 for k in ("sys_us", "user_us", "cpu_us", "wall_us"))
                and row["sys_us"] + row["user_us"] == row["cpu_us"] > 0 and row["wall_us"] > 0, "invalid wait4 row")
        directory = root / "runs" / f"{seq:03d}"
        if arm == "native":
            pids[pid] = (program,)
            if cutoff is None and name not in ENDPOINTS:
                cutoff = seq
            require(cutoff is None or name not in ENDPOINTS, "calibration follows a held-out native observation")
            case["native_ns"].append(row["cpu_us"] * 1000)
        elif arm == "ordinary":
            pids[pid] = METER
            record = directory / "records" / f"process-{pid}.jsonl"
            digest, report = read_profile(record.parent, pid, "current", reader)
            require(row["record_sha256"] == digest, "ordinary record digest mismatch")
            case["ordinary"] = {k: report[k] for k in ("instructions", "rep_bytes")}
            case["ordinary_us"] = row["cpu_us"]
            files.add(record)
        else:
            require(arm == "cachegrind", "unknown collector")
            pids[pid] = ("valgrind.bin", "cachegrind-amd64-linux")
            profile, log = directory / "cachegrind.out", directory / "cachegrind.log"
            require(sha(profile.read_bytes()) == row["cachegrind_sha256"]
                    and sha(log.read_bytes()) == row["cachegrind_log_sha256"], "Cachegrind receipt digest mismatch")
            bench.check_cachegrind_log(log, pid, program)
            case["cachegrind"] = bench.read_cachegrind(profile, program)
            case["cachegrind_us"] = row["cpu_us"]
            files.update((profile, log))
    require(cutoff is not None and all(len(c["native_ns"]) == 9 for group in cases.values() for c in group.values()),
            "missing native observations")
    require({f for f in (root / "runs").rglob("*") if f.is_file()} == files, "unexpected run receipt")
    verify_placement(root, pids)
    events, _ = parse_trace((root / "exec-trace").read_text())
    names = {exe for group in pids.values() for exe in group}
    require({pid for pid, trace in events.items() if any(v["executable"] in names for v in trace)} == set(pids),
            "unregistered measured process")
    seal_raw = (root / "predictions.json").read_bytes()
    seal = read_json(seal_raw)
    require(sha(seal_raw) == run["predictions_sha256"] and seal["schema"] == 1 and seal["plan_sha256"] == plan_sha
            and seal["boot_sha256"] == place["boot_sha256"] and seal["prefix_sha256"] == bench.rows_digest(rows[:cutoff]),
            "prediction seal does not bind the pre-label journal")
    holdouts = {key: group for key, group in cases.items() if key not in ENDPOINTS}
    require(set(seal["cases"]) == set(holdouts), "wrong sealed holdout set")
    for name, group in holdouts.items():
        require(set(seal["cases"][name]) == set(group), "wrong sealed programs")
        for program, case in group.items():
            a, b = (cases[n][program] for n in ENDPOINTS)
            prediction_input = {k: case[k] for k in ("ordinary", "cachegrind", "position_bits")}
            estimates = score.predict(a, b, prediction_input)
            registered_estimates = seal["cases"][name][program]
            require(registered_estimates == estimates and all(positive(v) for v in registered_estimates.values()),
                    "prediction differs from sealed forecast")
            case["predictions"] = estimates
            ranges = {}
            for method in ("distance", *score.FEATURES):
                values = [c["position_bits"] if method == "distance" else c["cachegrind"][score.FEATURES[method]]
                          for c in (a, b, case)]
                low, high = sorted(values[:2])
                if low == high:
                    ranges[method] = "equal_endpoints"
                elif values[2] < low:
                    ranges[method] = "below"
                elif values[2] > high:
                    ranges[method] = "above"
                else:
                    ranges[method] = "in_range"
            case["feature_ranges"] = ranges
            center = median(case["native_ns"])
            case["signed_error_ppm"] = {k: round(1_000_000 * Fraction(v - center, center)) for k, v in estimates.items()}
            case["native_tle_votes"] = {str(ms): sum(t > ms * 1_000_000 for t in case["native_ns"])
                                        for ms in plan["fixed_limits_ms"]}
    all_cases = [c for group in cases.values() for c in group.values()]
    total_cost = {k: sum(c[k] for c in all_cases) for k in ("ordinary_us", "cachegrind_us")}
    total_cost["median_cachegrind_over_ordinary_ppm"] = round(1_000_000 * median(
        Fraction(c["cachegrind_us"], c["ordinary_us"]) for c in all_cases))
    total_cost["median_cachegrind_over_native_ppm"] = round(1_000_000 * median(
        Fraction(1000 * c["cachegrind_us"], median(c["native_ns"])) for c in all_cases))
    budgets = plan["fixed_limits_ms"]
    return dict(schema=1, authority=plan["authority"], plan_sha256=plan_sha, run_sha256=sha(run_raw),
                predictions_sha256=sha(seal_raw), boot_sha256=place["boot_sha256"], cpu_steal_ticks=run["cpu_steal_ticks"],
                verified_pids=len(pids), sealed_prefix_rows=cutoff, cases=cases, profile_cost=total_cost,
                summary=score.score_cases([c for group in holdouts.values() for c in group.values()], budgets),
                programs={p: score.score_cases([group[p] for group in holdouts.values()], budgets)
                          for p in plan["binaries"]})


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("directory", type=Path)
    cli.add_argument("--output", type=Path)
    args = cli.parse_args()
    report = verify(args.directory)
    if args.output:
        args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report["summary"], indent=2))
