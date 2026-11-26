"""Replay measured CPU costs, query-order controls and preregistered tiled-input forecasts."""
import argparse
from collections import defaultdict
from fractions import Fraction
from functools import cache
import json
from math import ceil
from pathlib import Path
import re
from statistics import median
import sys
import tarfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from calibrated_limits.verify import METER, positive, verify_placement
from contest_submissions.bench import counters
from cpp20_locality.export import parse_trace
from cpp20_locality.verify import release_reader, require, sha
from first_run_sampler.profile import read_json
from guarded_native import anchor
from guarded_native.transfer import pair_certificate, scaled_ns, work
from scrivener_order import bench, corpus

PPM = 1_000_000


@cache
def historical_controls():
    """Read the two previously published control profiles, including their ELF and stream bindings."""
    controls = []
    for suffix in ("", "-repeat"):
        folder = HERE.parent / "results" / ("gce-contest-submissions" + suffix)
        archive = folder / "output.tar.gz"
        require(sha(archive.read_bytes()) == read_json((folder / "collection.json").read_bytes())["output_sha256"],
                "changed historical archive")
        current = {}
        with tarfile.open(archive, "r:gz") as capture:
            run = read_json(capture.extractfile("output/run.json").read())
            for row in run["rows"]:
                if row["case_id"] != "scrivener/interwoven_pages" or row["kind"] != "profile":
                    continue
                member = f"output/runs/{row['sequence']:03d}/records/process-{row['launcher_pid']}.jsonl"
                raw = capture.extractfile(member).read()
                require(sha(raw) == row["record_sha256"], "changed historical process record")
                end = read_json(raw.splitlines()[-1])
                require(end["event"] == "end" and end["valid"] is True, "incomplete historical record")
                current[row["program"]] = (row["elf_sha256"], row["stdin_sha256"], row["stdout_sha256"],
                                           dict(instructions=end["instructions"], rep_bytes=end["rep_bytes"],
                                                cycles_x16=end["latency_bound_x16"],
                                                memory_operand_bytes=end["memory_operand_bytes"]))
        controls.append(current)
    return controls


@cache
def inputs(plan_raw):
    stats = {}
    for case in read_json(plan_raw)["cases"]:
        name = case["name"]
        text, expected = corpus.make(name)
        require(sha(text.encode()) == case["stdin_sha256"] and sha(expected.encode()) == case["stdout_sha256"],
                "input or oracle differs from registration")
        positions = (int(line[2:]) for line in text.splitlines() if line.startswith("P "))
        previous = next(positions)
        bits = 0
        for position in positions:
            bits += abs(position - previous).bit_length()
            previous = position
        stats[name] = dict(**corpus.count_queries(text), successive_position_bits=bits)
    return stats


def forecast(ascending, tiled, shuffled, features, limits):
    a, b = ascending["native_median_ns"], shuffled["native_median_ns"]
    x, y, z = (features[name]["successive_position_bits"] for name in ("ascending", "tiled", "shuffled"))
    require(x < y < z, "query-order endpoints do not bracket the tiled input")
    weights = {key: Fraction(w) for key, w in anchor.weights().items()}
    # Use the first current cheap profile. Native labels from the tiled input enter only in scoring.
    estimates = dict(ascending=scaled_ns(a, work(tiled["current_counts"][0], weights),
                                         work(ascending["current_counts"][0], weights)),
                     interpolated=ceil(a + Fraction(y - x, z - x) * (b - a)), midpoint=ceil(Fraction(a + b, 2)))
    actual = tiled["native_median_ns"]
    out = dict(actual_ns=actual, ascending_only_ns=estimates["ascending"], interpolated_ns=estimates["interpolated"],
               midpoint_ns=estimates["midpoint"], limits={}, native_tle_votes={})
    correct = dict.fromkeys(estimates, 0)
    for key, value in estimates.items():
        out[key + "_error_ppm"] = round(PPM * Fraction(value - actual, actual))
    for ms in limits:
        threshold = ms * 1_000_000
        votes = sum(t > threshold for t in tiled["native_ns"])
        out["native_tle_votes"][str(ms)] = votes
        out["limits"][str(ms)] = {key + "_tle": value > threshold for key, value in estimates.items()}
        for key, value in estimates.items():
            correct[key] += (value > threshold) == (votes > len(tiled["native_ns"]) // 2)
    out["majority_correct"] = correct
    return out


def order_bound(ascending, shuffled):
    # Give every feature its most favorable observed value, independently,
    # including the ordinary-mode replicas for the instruction and REP counters.
    columns = ascending["counters"].keys()
    def extremes(case, fn):
        profiles = [case["counters"], *case["previous_counts"], *case["current_counts"]]
        return {key: fn(p[key] for p in profiles if key in p) for key in columns}

    proof = pair_certificate(min(shuffled["native_ns"]), max(ascending["native_ns"]),
                             extremes(shuffled, max), extremes(ascending, min))
    return proof[1] if proof else None


def verify(root):
    plan_raw = (HERE / "plan.json").read_bytes()
    plan = read_json(plan_raw)
    run = read_json((root / "run.json").read_bytes())
    place = run["placement"]
    require(run["schema"] == plan["schema"] == 1 and run["authority"] == plan["authority"] == "diagnostic_only"
            and run["plan_sha256"] == sha(plan_raw) and run["tools"] == plan["tools"], "wrong registration or tools")
    require(type(place["cpu"]) is int and place["cpu"] == 1 and run["cgroup_allowed_cpus"] == "1"
            and place["machine"] == "x86_64" and isinstance(place["kernel"], str) and place["kernel"]
            and re.fullmatch(r"[0-9a-f]{64}", place["boot_sha256"])
            and type(run["cpu_steal_ticks"]) is int and run["cpu_steal_ticks"] >= 0, "wrong guest placement")
    features = inputs(plan_raw)
    registered = {case["name"]: case for case in plan["cases"]}
    samples = defaultdict(lambda: defaultdict(list))
    reader = release_reader(plan["tools"]["reader"])
    pids, files = {}, set()
    for i, (row, slot) in enumerate(zip(run["rows"], bench.schedule(plan), strict=True)):
        require(type(row["sequence"]) is int and row["sequence"] == i
                and all(type(row[k]) is type(v) and row[k] == v for k, v in slot.items()), "unexpected schedule")
        pid, name, program, arm = row["launcher_pid"], slot["name"], slot["program"], slot["arm"]
        case = registered[name]
        require(row["elf_sha256"] == plan["binaries"][program] and row["stdin_sha256"] == case["stdin_sha256"]
                and row["stdout_sha256"] == case["stdout_sha256"], "wrong ELF, stream or answer")
        require(positive(pid) and pid not in pids and type(row["exit_code"]) is int and row["exit_code"] == 0
                and all(type(row[k]) is int and row[k] >= 0 for k in ("sys_us", "user_us", "cpu_us", "wall_us"))
                and row["sys_us"] + row["user_us"] == row["cpu_us"] > 0 and row["wall_us"] > 0, "invalid wait4 row")
        series = samples[name, program]
        series[arm].append(row["cpu_us"])
        if arm == "native":
            pids[pid] = (program,)
            require("record_sha256" not in row, "unexpected native profile digest")
            continue
        pids[pid] = METER
        directory = root / "runs" / f"{i:03d}" / "records"
        digest, parsed = bench.read_profile(directory, pid, arm, reader)
        require(row["record_sha256"] == digest, "profile digest differs from journal")
        files.add(directory / f"process-{pid}.jsonl")
        measured = counters(parsed) if arm == "diagnostic" else {k: parsed[k] for k in ("instructions", "rep_bytes")}
        series[arm + "_counts"].append(measured)
    require(files == {f for f in (root / "runs").rglob("*") if f.is_file()}, "unexpected archived process record")
    verify_placement(root, pids)
    events, _ = parse_trace((root / "exec-trace").read_text())
    workload_names = set(plan["binaries"]).union(METER)
    require({pid for pid, trace in events.items() if any(v["executable"] in workload_names for v in trace)} == set(pids),
            "unregistered workload in trace")

    cases = {name: {} for name in registered}
    for (name, program), series in samples.items():
        diagnostic, = series["diagnostic_counts"]
        cheap = {k: diagnostic[k] for k in ("instructions", "rep_bytes")}
        ratios = [Fraction(a, b) for a, b in zip(series["previous"], series["current"], strict=True)]
        times = [t * 1000 for t in series["native"]]
        cases[name][program] = dict(native_ns=times, native_median_ns=median(times), previous_us=series["previous"],
                                    current_us=series["current"], diagnostic_us=series["diagnostic"][0],
                                    counters=diagnostic, current_counts=series["current_counts"],
                                    previous_counts=series["previous_counts"],
                                    cheap_counters_match=all(c == cheap for c in
                                                             series["previous_counts"] + series["current_counts"]),
                                    previous_to_current_ratio_ppm=round(PPM * median(ratios)))
    estimates, matches, bounds = {}, {}, {}
    control = registered["interwoven_pages"]
    for program in plan["binaries"]:
        a, t, b = (cases[name][program] for name in ("ascending", "tiled", "shuffled"))
        estimates[program] = forecast(a, t, b, features, plan["fixed_limits_ms"])
        bounds[program] = order_bound(a, b)
        observed = (plan["binaries"][program], control["stdin_sha256"], control["stdout_sha256"],
                    cases["interwoven_pages"][program]["counters"])
        matches[program] = all(source[program] == observed for source in historical_controls())
    return dict(schema=1, authority="diagnostic_only", plan_sha256=sha(plan_raw), boot_sha256=place["boot_sha256"],
                cpu_steal_ticks=run["cpu_steal_ticks"], verified_pids=len(pids), cases=cases, input_features=features,
                order_forecast=estimates, archived_control_matches=matches, order_error_bounds=bounds)


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("directory", type=Path)
    cli.add_argument("--output", type=Path)
    args = cli.parse_args()
    report = verify(args.directory)
    if args.output:
        args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report["order_forecast"], indent=2))
