"""Replay the measured allocation-entry traffic and reference-relative CPU decisions."""
import argparse
from collections import Counter
from fractions import Fraction
import gzip
import json
from pathlib import Path
import re
import runpy

from collect import CAPACITY, HERE, NAMES, STUDIES, digest, read, require

LIMITS = (Fraction(1), Fraction(5, 4), Fraction(3, 2), Fraction(2), Fraction(3))
PENALTY = Fraction("6.92658480142496")


def parse_profile(stderr):
    lines = stderr.splitlines()
    entries = re.fullmatch(r"ENTRIES (\d+) (\d+) (\d+) (\d+) (\d+)", lines[0] if lines else "")
    balance = re.fullmatch(r"BALANCE (\d+) (\d+) (\d+)", lines[1] if len(lines) > 1 else "")
    requested = re.fullmatch(r"REQUESTED (\d+)", lines[2] if len(lines) > 2 else "")
    require(entries and balance and requested, "incomplete allocation receipt")
    calls = dict(zip(NAMES, map(int, entries.groups()), strict=True))
    final, peak, underflows = map(int, balance.groups())
    require(calls["__libc_realloc"] == underflows == 0 and 0 < final <= peak <= calls["__libc_malloc"] + calls["__libc_calloc"],
            "unsupported allocation accounting")
    sizes = {}
    for line in lines[3:]:
        match = re.fullmatch(r"SIZE (\d+) (\d+)", line)
        require(match is not None, "unknown allocation record")
        size, count = map(int, match.groups())
        require(size <= 128 and count > 0 and size not in sizes, "invalid or repeated request size")
        sizes[size] = count
    total = int(requested.group(1))
    require(0 < calls["_Znwm"] and sum(sizes.values()) <= calls["_Znwm"], "impossible size counts")
    require(sum(k * v for k, v in sizes.items()) <= total, "impossible request bytes")
    return dict(entries=calls, peak=peak, final=final, new_bytes=total, sizes=sizes)


def measured_profiles():
    doc = json.loads(gzip.decompress((HERE / "measurements.json.gz").read_bytes()))
    require(doc["client_source_sha256"] == digest(HERE / "entries.c") and doc["platform"] == "x86_64" and
            doc["cpu_profile"] == "local host", "different collector")
    reg = {r["path"]: r["sha256"] for r in read(CAPACITY / "milan/registration.json")["files"]}
    for path in ("bin64/drrun", "lib64/release/libdynamorio.so"):
        require(doc["runtime_sha256"][path] == reg["dynamorio/" + path], "different runtime")
    plans = {s: read(CAPACITY / s / "frozen.json")["cases"] for s in STUDIES}
    order = [(s, n) for s in STUDIES for n in plans[s]]
    require(len(order) == 33, "different case set")
    loaded, observed_elfs = {}, set()
    for row, expected in zip(doc["rows"], order + order[::-1], strict=True):
        study, name = expected
        case = plans[study][name]
        require((row["study"], row["case"]) == expected and row["exit_code"] == 0 and
                row["stdout_sha256"] == case["expected_sha256"] and row["stdout_bytes"] > 0 and
                all(row[k] == case[k] for k in ("stdin_sha256", "elf_sha256")), "changed local measurement")
        observed_elfs.add(case["elf_sha256"])
        counts = parse_profile(row["stderr"])
        if expected in loaded:
            require(row["repetition"] == 2 and loaded[expected] == counts, "repeated counts differ")
        else:
            require(row["repetition"] == 1, "wrong local schedule")
            loaded[expected] = counts
    require(set(doc["symbols"]) == observed_elfs and all(set(s) == set(NAMES) and
            len({int(v, 16) for v in s.values()}) == len(NAMES) and min(int(v, 16) for v in s.values()) > 0
            for s in doc["symbols"].values()), "incomplete symbol identities")
    return loaded


def original_panel(clocks, fixed_weight_group=None):
    milan, gshare = clocks["milan"], clocks["previous"]["gshare"]
    path = CAPACITY / "milan"
    reg, plan = read(path / "registration.json"), read(path / "frozen.json")
    reg["payload"] = {r["path"]: r["sha256"] for r in reg["files"]}
    branch = read(path / "gshare/records.json")
    gshare["evaluate"](branch)
    misses = {name: set() for name in plan["cases"]}
    for row in branch["rows"]:
        misses[row["case"]].add(gshare["restored"](row, plan["cases"][row["case"]])["gshare_mispredicts"])
    for phase, cloud in read(path / "evidence/cloud.json").items():
        data, _ = milan.read_boot(path / "evidence" / (phase + ".tar.gz"), phase, plan, reg, cloud)
        for group, names in plan["protocol"]["groups"].items():
            weights = {k: Fraction(str(v))*10**9 for k, v in plan["weights"][fixed_weight_group or group]["ML4"].items()}
            samples = {}
            for n in names:
                base, = {milan.predict(weights, milan.features(p)) for p in data[n]["probe"]}
                miss, = misses[n]
                samples[n] = (data[n]["native"], base, base + PENALTY * miss)
            yield group, phase, names, samples


def registered_panel(study, module, plan, reg, clocks):
    previous = clocks["previous"]
    weights = {k: Fraction(str(v)) * 10**9 for k, v in plan["ml4"].items()}
    clients = {arm: plan["clients"][name] for arm, name in plan["protocol"]["clients"].items()}
    evidence = CAPACITY / study / "evidence"
    for phase, cloud in read(evidence / "cloud.json").items():
        panel, _ = previous["read_boot"](phase, cloud, reg, plan, evidence=evidence, freeze=module["FREEZE"],
            client_by_arm=clients, diagnostic_by_arm={"outer": "outer", "flat": "outer", "branch": "latency"})
        previous["read_branch"](evidence / (phase + ".tar.gz"), panel)
        samples = {}
        for n, arms in panel.items():
            scores, _ = clocks["measured"](arms, weights, PENALTY)
            base, = set(scores["ML4"])
            hybrid, = set(scores["ML4_plus_misses"])
            samples[n] = ([r["cpu_us"] for r in arms["native"]], base, hybrid)
        for group, names in plan["protocol"]["groups"].items():
            family = study
            if study == "order_statistics_transfer":
                family = "predecessor" if group in plan["protocol"]["training_examples"] else "inversions"
            yield study, family, phase, names, samples


def new_panels(clocks, graph):
    previous = clocks["previous"]
    for study, module in zip(STUDIES[1:], (previous, clocks, graph), strict=True):
        registration = module["registered"]()
        if study == "order_statistics_transfer":
            reg, plan, _ = registration
        else:
            plan, reg = registration
        yield from registered_panel(study, module, plan, reg, clocks)


def equality_cost(score, ref_score, count, ref_count, limit=2):
    slope = count - limit * ref_count
    require(slope > 0, "more cost cannot raise this score through the deadline")
    return Fraction(limit * ref_score - score, slope)


def decision(truth, predicted, factor):
    low, high = truth
    if low <= factor < high:
        return "native_mixed"
    if (predicted <= factor) == (high <= factor):
        return "correct"
    return "false_accept" if predicted <= factor else "false_timeout"


def replay():
    profiles = measured_profiles()
    graph = runpy.run_path(str(CAPACITY / "dijkstra_relaxation/replay.py"))
    clocks = graph["clocks"]
    old = list(original_panel(clocks))
    bounds = {}
    for group, phase, (ref, _, alt), samples in old:
        if phase != "primary" or group not in ("dijkstra", "bfs_grid"):
            continue
        a, b = profiles["milan", alt], profiles["milan", ref]
        native = clocks["milan"].relative_verdict(samples[alt][0], samples[ref][0], 2)
        require(native == ("timeout" if group == "dijkstra" else "accept"), "different native 2x control")
        bounds[group] = equality_cost(samples[alt][1], samples[ref][1], a["entries"]["_Znwm"], b["entries"]["_Znwm"])
        if group == "dijkstra":
            cutoff = equality_cost(samples[alt][2], samples[ref][2], a["peak"], b["peak"])
    coefficient = cutoff.numerator // cutoff.denominator + 1
    groups = [("milan", group, phase, names, samples) for group, phase, names, samples in old if group == "dijkstra"]
    groups.extend(new_panels(clocks, graph))
    transfers = {}
    for study, family, phase, (ref, *others), samples in groups:
        phase_result = transfers.setdefault(family, {}).setdefault(phase, {})
        for name in others:
            target, _, base = samples[name]
            anchor, _, ref_base = samples[ref]
            native = Fraction(min(target), max(anchor)), Fraction(max(target), min(anchor))
            point = base / ref_base
            charged = (base + coefficient * profiles[study, name]["peak"]) / (ref_base + coefficient * profiles[study, ref]["peak"])
            phase_result[name] = dict(native=[str(x) for x in native], baseline=str(point), candidate=str(charged),
                baseline_outcomes=[decision(native, point, f) for f in LIMITS],
                candidate_outcomes=[decision(native, charged, f) for f in LIMITS])
    totals = {family: {phase: {label: dict(Counter(v for case in cases.values() for v in case[label]))
                               for label in ("baseline_outcomes", "candidate_outcomes")}
                       for phase, cases in phases.items()} for family, phases in transfers.items()}
    return dict(new_cost_equality_ns={k: str(v) for k, v in bounds.items()}, peak_cost_equality_ns=str(cutoff),
                peak_cost_ns=coefficient, deadlines=[str(x) for x in LIMITS], counts=totals, cases=transfers)


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--verify", type=Path)
    args = cli.parse_args()
    report = replay()
    if args.verify:
        require(report == read(args.verify), "published allocator transfer results differ")
        print("Allocation observations and all relative decisions matched.")
    else:
        print(json.dumps(report, indent=2))
