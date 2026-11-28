"""Compare the complete full-cache reports and CPU time of two registered clients."""
import argparse
import json
from pathlib import Path
import statistics
import sys
import tarfile

import prepare

HERE = prepare.HERE
sys.path.insert(0, str(prepare.CONTROL.parent / "graph_stage_cost"))
from graph_evidence import milan, read_boot, require, sha

FREEZE = "685248203bd010664b2f6d343ed475c604255359"
REG = "df08a366c9bce671d9b91f4098b2206e120609bdf64b18b0c9a2766d9333464f"
PHASES = ("primary", "repeat")
ARMS = ("native", "outer", "flat_outer")
GRAPH = HERE.parent / "aonohako_shortest_paths"


def read(path):
    return json.loads(path.read_bytes())


def registered():
    reg, plan = (read(HERE / name) for name in ("registration.json", "frozen.json"))
    parent = read(prepare.CONTROL / "registration.json")["payload"]
    prior = read(prepare.CONTROL / "frozen.json")
    require(sha((HERE / "registration.json").read_bytes()) == REG and
            sha((HERE / "frozen.json").read_bytes()) == reg["payload"]["plan.json"] and
            reg["expected_rows"] == {".": 45}, "different study registration")
    protocol = plan["protocol"]
    require(protocol == read(HERE / "protocol.json") and
            protocol["modes"]["outer"] == protocol["modes"]["flat_outer"] ==
            ["-rep_bytes", "-latency_bound", "-probe_l1d", "-probe_outer"] and
            list(protocol["modes"]) == list(ARMS) and plan["cases"] == prior["cases"] and
            list(plan["cases"]) == protocol["programs"] and
            all(protocol["rounds"].count(arm) == 5 for arm in ARMS), "changed graph schedule")
    require(set(reg["payload"]) == (set(parent) - {"callback.so"}) | {"flat.so"} and
            all(reg["payload"][k] == h for k,h in parent.items() if k not in ("callback.so", "plan.json")) and
            all(reg["payload"][k] == h for k,h in plan["clients"].items()) and
            plan["clients"]["flat.so"] != plan["clients"]["original.so"], "changed tool or guest payload")
    return reg, plan


def ml4(raw, keys):
    outer = raw["outer"]
    fields = milan.features(dict(instructions=raw["instructions"], movs_bytes=raw["movs_bytes"],
        stos_bytes=raw["stos_bytes"], latency_bound_x16=raw["latency_x16"], l1d_misses=raw["l1d_misses"],
        outer_misses=outer[4:7], outer_stream=outer[7:10], outer_dependent=outer[10:13], l1d_dependent=outer[13]))
    return {k: fields[k] for k in keys}


def historical_features(keys):
    plan, reg = read(GRAPH / "frozen.json"), read(GRAPH / "registration.json")
    cloud = read(GRAPH / "evidence/cloud.json")
    seen = {}
    for p in PHASES:
        boot, _ = milan.read_boot(GRAPH / "evidence" / (p + ".tar.gz"), p, plan, reg, cloud[p])
        for n,runs in boot.items():
            for item in runs["probe"]:
                feature = {k: milan.features(item)[k] for k in keys}
                require(seen.setdefault(n, feature) == feature, "historical ML4 is not stable")
    return seen


def summarize(arms, old):
    times = {a: [r["cpu_us"] for r in rows] for a,rows in arms.items()}
    med = {a: statistics.median(t) for a,t in times.items()}
    common = arms["outer"][0]["counters"]
    different = set()
    for arm in ("outer", "flat_outer"):
        for row in arms[arm]:
            observed = row["counters"]
            different.update(k for k in set(common) | set(observed) if common.get(k) != observed.get(k))
    projected = ml4(common, old)
    matches = all(ml4(row["counters"], old) == projected for arm in ("outer", "flat_outer") for row in arms[arm])
    return dict(median_cpu_us=med, cpu_us_ranges={k: [min(v), max(v)] for k,v in times.items()},
                original_over_flat=round(med["outer"] / med["flat_outer"], 4),
                all_pair_speedup=[round(min(times["outer"]) / max(times["flat_outer"]), 4),
                                  round(max(times["outer"]) / min(times["flat_outer"]), 4)],
                flat_over_native=round(med["flat_outer"] / med["native"], 4),
                original_over_native=round(med["outer"] / med["native"], 4),
                different_counter_fields=sorted(different), all_eight_ml4_equal=matches,
                ml4_counts=projected, original_matches_earlier_graph=projected == old)


def receipt_hashes():
    """Compare entire stderr and all stream fields except per-process identity."""
    by_case = {}
    for phase in PHASES:
        with tarfile.open(HERE / "evidence" / (phase + ".tar.gz"), "r:gz") as archive:
            for row in map(json.loads, archive.extractfile("output/trials.jsonl")):
                if row["arm"] == "native":
                    continue
                i, pid = row["index"], row["pid"]
                path = f"output/records-{i:03d}/process-{pid}.jsonl"
                normalized = [{k: v for k,v in record.items() if k not in ("pid", "segment")}
                              for record in map(json.loads, archive.extractfile(path))]
                stderr = archive.extractfile(f"output/probe-{i:03d}.txt").read()
                streams, diagnostics = by_case.setdefault(row["case"], (set(), set()))
                streams.add(sha(json.dumps(normalized, sort_keys=True).encode()))
                diagnostics.add(sha(stderr))
    return {n: dict(normalized_work=sorted(records), cache_stderr=sorted(stderr))
            for n,(records,stderr) in by_case.items()}


def replay():
    reg, plan = registered()
    cloud = read(HERE / "evidence/cloud.json")
    for field in ("instance_id", "boot_sha256", "boot_disk_id"):
        require(len({v[field] for v in cloud.values()}) == 2, "guest identity reused")
    keys = read(GRAPH / "frozen.json")["ml4"]
    require(len(keys) == 8, "different frozen model")
    history = historical_features(keys)
    clients = {a: plan["clients"][n] for a,n in plan["protocol"]["clients"].items()}
    panels, result = {}, {}
    for phase in PHASES:
        panels[phase], steal = read_boot(phase, cloud[phase], reg, plan, evidence=HERE / "evidence", freeze=FREEZE,
                                        client_by_arm=clients, diagnostic_by_arm={"outer": "outer", "flat_outer": "outer"})
        result[phase] = dict(steal_ticks=steal,
                             cases={n: summarize(a, history[n]) for n,a in panels[phase].items()})
    both = {n: {a: panels["primary"][n][a] + panels["repeat"][n][a] for a in ARMS} for n in plan["cases"]}
    result["pooled"] = {n: summarize(a, history[n]) for n,a in both.items()}
    hashes = receipt_hashes()
    result["receipt_hashes"] = hashes
    result["counter_equivalent"] = all(not r["different_counter_fields"] and r["all_eight_ml4_equal"]
                                       and all(len(v) == 1 for v in hashes[n].values())
                                       for n,r in result["pooled"].items())
    return result


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--verify", type=Path)
    opts = cli.parse_args()
    report = replay()
    if opts.verify:
        require(report == read(opts.verify), "published results differ")
        print("Verified registered ELF, placement, full receipts, medians and speedup bounds on both guests.")
    else:
        print(json.dumps(report, indent=2))
