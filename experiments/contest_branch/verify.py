"""Rebuild forecasts, costs and deadline votes from an IOI Wall guest archive."""
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
from contest_branch import bench, corpus, score
from cpp20_locality.export import parse_trace
from cpp20_locality.verify import release_reader, require, sha
from first_run_sampler.profile import read_json
from scrivener_cache.score import score_cases
from scrivener_order.bench import read_profile


@cache
def registered_cases(raw, corpus_module=corpus, reference_counts=None):
    registered = {r["name"]: r for r in read_json(raw)["cases"]}
    require(tuple(registered) == corpus_module.NAMES, "wrong Wall input set")
    for name, case in registered.items():
        text, answer = corpus_module.make(name)
        require(sha(text.encode()) == case["stdin_sha256"] and sha(answer.encode()) == case["stdout_sha256"],
                "generator differs from registered Wall input or oracle")
        if reference_counts is not None:
            expected = "{accesses} {misses}\n".format(**reference_counts(text)).encode()
            require(sha(expected) == case["probe_stdout_sha256"], "registered input probe differs from reference")
    return registered


def verify(root):
    return verify_study(root, HERE, corpus, score, bench.PROFILE_ARMS)


def verify_study(root, study, corpus_module, score_module, profile_arms, *, read_probe=None, reference_counts=None):
    plan_raw = (study / "plan.json").read_bytes()
    plan = read_json(plan_raw)
    run_raw = (root / "run.json").read_bytes()
    run = read_json(run_raw)
    plan_sha = sha(plan_raw)
    require(run["plan_sha256"] == plan_sha and run["schema"] == plan["schema"] == 1
            and run["authority"] == plan["authority"] == "diagnostic_only" and run["tools"] == plan["tools"],
            "wrong registration or tools")
    require(plan["cache_geometry"] == {k: [v, 8, 64] for k, v in bench.CACHES.items()}, "wrong cache geometry")
    place = run["placement"]
    require(type(place["cpu"]) is int and place["cpu"] == 1 and run["cgroup_allowed_cpus"] == "1"
            and place["machine"] == "x86_64" and re.fullmatch(r"[0-9a-f]{64}", place["boot_sha256"])
            and bool(place["kernel"]) and type(run["cpu_steal_ticks"]) is int and run["cpu_steal_ticks"] >= 0,
            "invalid placement")
    registered = registered_cases(plan_raw, corpus_module, reference_counts)
    cases = {n: dict(native_ns=[], profiles={arm: [] for arm in profile_arms}) for n in registered}
    reader = release_reader(plan["tools"]["reader"])
    rows = run["rows"]
    files, pids = set(), {}
    cutoff = None
    for seq, (row, slot) in enumerate(zip(rows, bench.schedule(plan, profile_arms), strict=True)):
        require(type(row["sequence"]) is int and row["sequence"] == seq
                and all(type(row[k]) is type(v) and row[k] == v for k, v in slot.items()), "wrong schedule")
        name, program, arm = slot["name"], slot["program"], slot["arm"]
        case = cases[name]
        probe_run = arm == "leaf_pages"
        expected_elf = plan["probe"]["sha256"] if probe_run else plan["binaries"][program]
        output_key = "probe_stdout_sha256" if probe_run else "stdout_sha256"
        require(program == corpus_module.PROGRAM and row["elf_sha256"] == expected_elf
                and row["stdin_sha256"] == registered[name]["stdin_sha256"]
                and row["stdout_sha256"] == registered[name][output_key], "wrong binary or I/O")
        pid = row["launcher_pid"]
        require(positive(pid) and pid not in pids and type(row["exit_code"]) is int and row["exit_code"] == 0
                and all(type(row[k]) is int and row[k] >= 0 for k in ("user_us", "sys_us", "cpu_us", "wall_us"))
                and row["user_us"] + row["sys_us"] == row["cpu_us"] > 0 and row["wall_us"] > 0, "invalid wait4 row")
        directory = root / "runs" / f"{seq:03d}"
        if arm == "native":
            pids[pid] = (program,)
            if name not in plan["anchors"] and cutoff is None:
                cutoff = seq
            case["native_ns"].append(row["cpu_us"] * 1000)
            continue
        if arm == "ordinary":
            pids[pid] = METER
            path = directory / "records" / f"process-{pid}.jsonl"
            digest, record = read_profile(path.parent, pid, "current", reader)
            require(row["record_sha256"] == digest, "ordinary record digest mismatch")
            counts = {k: record[k] for k in ("instructions", "rep_bytes")}
            files.add(path)
        elif probe_run:
            pids[pid] = (Path(plan["probe"]["path"]).name,)
            path = directory / "leaf_pages.out"
            require(sha(path.read_bytes()) == row["stdout_sha256"], "input probe receipt digest mismatch")
            counts = read_probe(path)
            files.add(path)
        else:
            pids[pid] = ("valgrind.bin", "cachegrind-amd64-linux")
            profile, log = directory / "cachegrind.out", directory / "cachegrind.log"
            require(sha(profile.read_bytes()) == row["cachegrind_sha256"] and
                    sha(log.read_bytes()) == row["cachegrind_log_sha256"], "simulator digest mismatch")
            bench.check_cachegrind_log(log, pid, program)
            parser = bench.read_cachegrind if arm == "full" else bench.read_branch_profile
            counts = parser(profile, program)
            files.update((profile, log))
        case["profiles"][arm].append(dict(cpu_us=row["cpu_us"], counts=counts))

    require(files == {f for f in (root / "runs").rglob("*") if f.is_file()}, "unexpected process receipt")
    verify_placement(root, pids)
    events, _ = parse_trace((root / "exec-trace").read_text())
    names = {exe for chain in pids.values() for exe in chain}
    require({p for p, trace in events.items() if any(v["executable"] in names for v in trace)} == set(pids),
            "unregistered measured process")
    seal_raw = (root / "predictions.json").read_bytes()
    seal = read_json(seal_raw)
    require(cutoff is not None and sha(seal_raw) == run["predictions_sha256"] and seal["schema"] == 1
            and seal["boot_sha256"] == place["boot_sha256"] and seal["plan_sha256"] == plan_sha
            and seal["prefix_sha256"] == bench.rows_digest(rows[:cutoff]), "invalid pre-label forecast seal")
    held = {name: c for name, c in cases.items() if name not in plan["anchors"]}
    require(held.keys() == seal["cases"].keys(), "wrong sealed holdouts")

    def first_counts(c):
        return {k: obs[0]["counts"] for k, obs in c["profiles"].items()}

    anchors = [dict(first_counts(cases[n]), native_ns=cases[n]["native_ns"]) for n in plan["anchors"]]
    for name, case in held.items():
        current = first_counts(case)
        estimates = score_module.predict(*anchors, current)
        sealed = seal["cases"][name]
        require(set(sealed) == {corpus_module.PROGRAM} and all(positive(v) for v in sealed[corpus_module.PROGRAM].values())
                and sealed[corpus_module.PROGRAM] == estimates, "replayed prediction differs from sealed forecast")
        case["predictions"] = estimates
        center = median(case["native_ns"])
        case["signed_error_ppm"] = {k: round(1_000_000 * Fraction(v - center, center)) for k, v in estimates.items()}
        case["native_tle_votes"] = {str(ms): sum(t > ms * 1_000_000 for t in case["native_ns"])
                                    for ms in plan["fixed_limits_ms"]}
        ranges = {}
        features = [("branch_only", "Bcm"), ("full", "DLmr")]
        if read_probe is not None:
            features.append(("leaf_pages", "misses"))
        for arm, event in features:
            low, high = sorted(a[arm][event] for a in anchors)
            x = current[arm][event]
            ranges[event] = ("equal_endpoints" if low == high else "below" if x < low
                             else "above" if x > high else "in_range")
        case["feature_ranges"] = ranges

    ratios = {key: [] for key in ("full", "ordinary", "native")}
    for case in cases.values():
        times = {arm: median(Fraction(o["cpu_us"]) for o in obs) for arm, obs in case["profiles"].items()}
        times["native"] = Fraction(median(case["native_ns"]), 1000)
        for key, values in ratios.items():
            values.append(Fraction(times["branch_only"], times[key]))
        case["paired_events_equal"] = {
            event: [f["counts"][event] == b["counts"][event] for f, b in
                    zip(case["profiles"]["full"], case["profiles"]["branch_only"], strict=True)]
            for event in bench.BRANCH_EVENTS}
    costs = {f"median_branch_only_over_{k}_ppm": round(1_000_000 * median(v)) for k, v in ratios.items()}
    costs["total_cpu_us"] = {k: sum(o["cpu_us"] for c in cases.values() for o in c["profiles"][k])
                             for k in profile_arms}
    return dict(schema=1, authority=plan["authority"], plan_sha256=plan_sha, run_sha256=sha(run_raw),
                predictions_sha256=sha(seal_raw), boot_sha256=place["boot_sha256"], cpu_steal_ticks=run["cpu_steal_ticks"],
                verified_pids=len(pids), sealed_prefix_rows=cutoff, cases=cases, profile_cost=costs,
                summary=score_cases(list(held.values()), plan["fixed_limits_ms"], score_module.METHODS))


def main(replay=verify):
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("directory", type=Path)
    cli.add_argument("--output", type=Path)
    args = cli.parse_args()
    report = replay(args.directory)
    if args.output:
        args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    main()
