"""Compare standard latency, the non-flat control and the original L1D path."""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import statistics
import sys

import prepare

HERE = prepare.HERE
sys.path.insert(0, str(prepare.STAGES))
from graph_evidence import read_boot, require, sha

FREEZE = "da50cbe17d145f4ab19ff9c065bde59c3f4235f0"
REG = "0018d5d25c44c4aec670151f6e71db970fe2c52356be8960e02ca6e258ea0958"
PHASES = ("primary", "repeat")
MODES = ("latency", "callback", "l1d")


def read(path):
    return json.loads(path.read_bytes())


def registered():
    reg = read(HERE / "registration.json")
    plan = read(HERE / "frozen.json")
    parent = read(prepare.STAGES / "registration.json")
    previous = read(prepare.STAGES / "frozen.json")
    require(sha((HERE / "registration.json").read_bytes()) == REG and
            sha((HERE / "frozen.json").read_bytes()) == reg["payload"]["plan.json"] and
            sha((HERE / "guest.py").read_bytes()) == reg["payload"]["guest.py"] and
            read(HERE / "protocol.json") == plan["protocol"] and reg["expected_rows"] == {".":45},
            "different experiment registration")
    require(list(plan["protocol"]["modes"]) == list(MODES) and plan["cases"] == previous["cases"] and
            plan["clients"]["original.so"] == previous["client_sha256"] and
            all(reg["payload"][name] == digest for name,digest in plan["clients"].items()),
            "different ELF panel or installed clients")
    for name,digest in reg["payload"].items():
        if name not in ("guest.py", "plan.json", "callback.so"):
            source = {"original.so":"probe.so", "base_guest.py":"guest.py"}.get(name, name)
            require(parent["payload"][source] == digest, "different original payload member")
    return reg, plan


def summarize(arms):
    times = {a:[r["cpu_us"] for r in runs] for a,runs in arms.items()}
    med = {a:statistics.median(t) for a,t in times.items()}
    total = med["l1d"] - med["latency"]
    require(total > 0, "unexpected nonpositive total difference")
    unique = defaultdict(set)
    for sample in arms.values():
        for row in sample:
            for field, value in row["counters"].items():
                unique[field].add(value)
    changes = {}
    for a,b in zip(MODES, MODES[1:]):
        difference = med[b] - med[a]
        changes[b] = dict(median_delta_us=difference,
                          all_pair_delta_us=[min(times[b])-max(times[a]), max(times[b])-min(times[a])],
                          percent_l1d_minus_latency=round(100*difference/total, 3))
    return dict(samples={a:len(t) for a,t in times.items()},
                cpu_us={a:[min(t), med[a], max(t)] for a,t in times.items()},
                distinct_readings_per_counter={key:len(v) for key,v in unique.items()}, changes=changes)


def replay():
    reg, plan = registered()
    cloud = read(HERE / "evidence/cloud.json")
    earlier = [v for location in (prepare.STAGES, prepare.stages.PARENT)
               for v in read(location / "evidence/cloud.json").values()]
    for key in ("instance_id", "boot_disk_id", "boot_sha256"):
        ids = {h[key] for h in cloud.values()}
        require(len(ids) == 2 and ids.isdisjoint(h[key] for h in earlier), "reused " + key)
    clients = {arm:plan["clients"][file] for arm,file in plan["protocol"]["clients"].items()}
    panels, result = {}, {}
    for phase in PHASES:
        panel, steal = read_boot(phase, cloud[phase], reg, plan, evidence=HERE / "evidence",
                                 freeze=FREEZE, client_by_arm=clients)
        panels[phase] = panel
        result[phase] = dict(steal_ticks=steal, cases={n:summarize(a) for n,a in panel.items()})
    pooled = {n:{arm:panels["primary"][n][arm] + panels["repeat"][n][arm] for arm in MODES}
              for n in plan["cases"]}
    result["pooled"] = {n:summarize(arms) for n,arms in pooled.items()}
    return result


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--verify", type=Path)
    args = cli.parse_args()
    result = replay()
    if args.verify:
        require(result == read(args.verify), "published results differ")
        print("Verified both paired controls, receipt identities, counts and CPU intervals.")
    else:
        print(json.dumps(result, indent=2, sort_keys=True))
