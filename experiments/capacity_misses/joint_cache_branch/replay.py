"""Compare every joint counter and report single-pass collection cost per guest."""
import argparse
from collections import defaultdict
from fractions import Fraction
import json
from pathlib import Path
import runpy
from statistics import median
import tarfile

HERE = Path(__file__).resolve().parent
MATCHERS = HERE.parent / "string_matching"
source = runpy.run_path(str(MATCHERS / "replay.py"))
clock = source["clock"]["previous"]
read, require, sha = source["read"], source["require"], source["sha"]
FREEZE = "f31495731feefad5346da7df327cba2d997f6657"
REG = "f7e5a296a54d12ac12b2b214f435dbf04ae6203e74c8f157d829c08731acf866"


def registered():
    old_reg, original = source["registered"]()
    reg, plan = read(HERE / "registration.json"), read(HERE / "frozen.json")
    payload = reg["payload"]
    require(sha((HERE / "registration.json").read_bytes()) == REG and
            sha((HERE / "frozen.json").read_bytes()) == payload["plan.json"] and
            sha((HERE / "guest.py").read_bytes()) == payload["guest.py"] and
            read(HERE / "protocol.json") == plan["protocol"], "changed study registration")
    require(set(payload) == set(old_reg["payload"]) and all(h == old_reg["payload"][name] for name, h in
            payload.items() if name not in ("plan.json", "guest.py")), "changed registered tools, ELFs or inputs")
    require(all(plan[k] == original[k] for k in ("cases", "clients", "ml4", "gshare_miss_ns")) and
            reg["expected_rows"] == {".": 84}, "different cases or clock")
    return plan, reg, original, old_reg


def load(directory, phase, host, plan, reg, freeze):
    evidence = directory / "evidence"
    by_arm = {arm:plan["clients"][name] for arm,name in plan["protocol"]["clients"].items()}
    rows, steal = clock["read_boot"](phase, host, reg, plan, evidence=evidence, freeze=freeze,
        client_by_arm=by_arm, diagnostic_by_arm={"branch":"latency", "flat":"outer", "outer":"outer", "joint":"outer"})
    indexes = defaultdict(int)
    with tarfile.open(evidence / (phase + ".tar.gz")) as archive:
        expected_files = set()
        for row in map(json.loads, archive.extractfile("output/trials.jsonl")):
            arm, name = row["arm"], row["case"]
            if arm not in ("branch", "joint"):
                continue
            path = f"output/branch-{row['index']:03d}.json"
            expected_files.add(path)
            sample = rows[name][arm][indexes[name, arm]]
            indexes[name, arm] += 1
            predictor = json.load(archive.extractfile(path))
            clock["gshare"]["branch_counts"](predictor, sample["counters"])
            sample["predictor"] = predictor
        require({p for p in archive.getnames() if p.startswith("output/branch-")} == expected_files,
                "unexpected predictor records")
    return rows, steal


def different(records):
    """Report mismatching or missing fields without dropping unsuccessful criteria."""
    keys = set.union(*(set(row) for row in records))
    return sorted(k for k in keys if any(k not in row or row[k] != records[0].get(k) for row in records))


def compare(arms, historical):
    joint = arms["joint"]
    cache = [r["counters"] for r in joint + arms["flat"]]
    branch = [r["predictor"] for r in joint + arms["branch"]]
    shared = [r["counters"] for r in arms["branch"]]
    shared_fields = set(shared[0])
    shared.extend({k:r["counters"][k] for k in shared_fields} for r in joint)
    diffs = dict(cache=different(cache), predictor=different(branch), shared_work=different(shared),
                 historical_cache=different(cache + [r["counters"] for r in historical["flat"] + historical["outer"]]),
                 historical_predictor=different(branch + [r["predictor"] for r in historical["branch"]]))
    times = {arm:median([r["cpu_us"] for r in runs]) for arm,runs in arms.items()}
    separate = times["flat"] + times["branch"]
    ratio = Fraction(times["joint"], separate)
    return dict(different_fields=diffs, cpu_median_us=times, separate_cpu_us=separate,
                joint_over_separate=str(ratio), saved_percent=round(float(1-ratio)*100, 3),
                joint_over_native=round(times["joint"]/times["native"], 3),
                counters_match=not any(diffs.values()), faster=ratio < 1)


def replay():
    plan, reg, original, old_reg = registered()
    cloud = read(HERE / "evidence/cloud.json")
    old_cloud = read(MATCHERS / "evidence/cloud.json")
    for key in ("instance_id", "boot_disk_id", "boot_sha256"):
        require(len({h[key] for meta in (cloud, old_cloud) for h in meta.values()}) == 4, "reused guest identity")
    phases = {}
    for phase in plan["protocol"]["phases"]:
        old, _ = load(MATCHERS, phase, old_cloud[phase], original, old_reg, source["FREEZE"])
        samples, steal = load(HERE, phase, cloud[phase], plan, reg, FREEZE)
        cases = {name:compare(arms, old[name]) for name, arms in samples.items()}
        phases[phase] = dict(cases=cases, steal_ticks=steal,
                             counter_criterion=all(c["counters_match"] for c in cases.values()),
                             cpu_criterion=all(c["faster"] for c in cases.values()))
    return dict(boots=phases, passes_both=all(v["counter_criterion"] and v["cpu_criterion"] for v in phases.values()))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
    result = replay()
    if args.verify:
        require(result == read(args.verify), "published joint results differ")
        print("Verified the new and historical archives, exact field comparisons and per-case CPU medians.")
    else:
        print(json.dumps(result, indent=2))
