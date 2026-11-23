"""Replay frozen selections, paid captures, real deadline observations and paired preparation costs."""
import argparse
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from calibrated_limits.verify import METER, live_observation, load, positive, verify_placement
from cpp20_locality.export import parse_trace
from cpp20_locality.verify import release_reader, require, sha
from first_run_sampler.profile import digest, uint
from footprint_admission.verify import select
from guarded_native import anchor
from preflight_cache import session
from shape_calibration import combined, temporal
from shape_calibration import verify as prior


class Accounting:
    def __init__(self):
        self.last = None
        self.pids = {}

    def duration(self, raw):
        a, b = raw["before"], raw["after"]
        fields = {"wall_ns", "self_user_us", "self_sys_us", "children_user_us", "children_sys_us"}
        require(set(a) == set(b) == fields and all(uint(a[k]) and uint(b[k]) and a[k] <= b[k]
                and (self.last is None or self.last[k] <= a[k]) for k in fields)
                and b["wall_ns"] > a["wall_ns"], "invalid or overlapping time readings")
        self.last = b
        def cpu_ns(who):
            return sum(b[who + k] - a[who + k] for k in ("_user_us", "_sys_us")) * 1000
        own, child = cpu_ns("self"), cpu_ns("children")
        return dict(wall_ns=b["wall_ns"] - a["wall_ns"], self_cpu_ns=own, children_cpu_ns=child, cpu_ns=own + child)

    def launch(self, pid, commands):
        require(positive(pid) and pid not in self.pids, "duplicate measured PID")
        self.pids[pid] = commands


def profile(case, cap, probe_sha):
    full = prior.check_full(case, cap / "full")
    obs = full["observation"]
    order = combined.inspect_temporal(cap, case["binary_sha256"], sha(case["stdin"].encode()),
                                      sha(case["stdout"].encode()), probe_sha)
    require(prior.profile_cpu_ns(cap / "full") == order["observation"]["cpu_ns"], "combined CPU disagreement")
    return (full, order), obs, order["observation"]["cpu_ns"]


def finish_live(root, slot, case, plan, policy, reader, acct, selection, obs, selection_sha):
    raw = load(root / "live/result.json")
    expected = dict(**raw, selection_sha256=selection_sha, method=selection["method"], action=selection["action"],
                    reference_index=selection["reference_index"], current_sample_sha256=obs["sample_sha256"],
                    current_result_sha256=obs["result_sha256"], preflight_prediction_ns=selection["prediction_ns"])
    require(load(root / "result.json") == expected, "live receipt bound to another capture or policy")
    result = live_observation(root / "live", dict(kind="limited", **slot), case, plan, policy, reader)
    acct.launch(result["pid"], METER)
    return dict(limit_ms=slot["limit_ms"], pid=result["pid"], observation=result["observation"],
                tle=result["tle"], prediction_ns=result["prediction_ns"], timing=acct.duration(slot["timing"]))


def check_cache_entry(cap, case, kernel, bank, probe_sha):
    record, receipt = session.checked_entry(cap)
    identity = record["identity"]
    expected = dict(schema=1, session=load(cap.parent / "session.json")["id"], cpu=1, kernel=kernel,
                    protocol_sha256=sha(anchor.PROTOCOL.read_bytes()), probe_sha256=probe_sha,
                    reference_sha256=anchor.REFERENCE_SHA, elf_sha256=case["binary_sha256"], method="footprint",
                    stdin_sha256=sha(case["stdin"].encode()),
                    references=[dict(key=p["key"], receipt_sha256=p["receipt"]) for p in bank])
    require(all(identity[k] == v for k, v in expected.items())
            and all(digest(identity[k]) for k in ("session", "boot_sha256", "implementation_sha256", "invocation_sha256")),
            "cached identity differs from its guest, input or paid bank")
    for seed in bank:
        require(all(seed["identity"][k] == value for k, value in identity.items() if k not in ("stdin_sha256", "references")),
                "paid reference belongs to a different session context")
    return identity, receipt


def check_prepared(root, row, case, kernel, bank, seen, acct, probe_sha, fresh_path=None):
    prepared = row["prepared"]
    timing = acct.duration(row["timing"])
    hit, key = prepared["hit"], prepared["key"]
    require(type(hit) is bool, "invalid hit indicator")
    if fresh_path is None:
        require(digest(key), "invalid session key")
        cap = root / "cache" / key
        item = (case["case_id"], tuple(p["key"] for p in bank))
        require(hit == (item in seen) and (seen[item] == key if hit else key not in seen.values()),
                "miss/hit or key conflicts with session history")
        seen[item] = key
        identity, receipt = check_cache_entry(cap, case, kernel, bank, probe_sha)
    else:
        require(not hit and key is None, "uncached preparation claims a cache entry")
        cap = fresh_path
        identity, receipt = None, None
    require(prepared["capture"] == str(cap.relative_to(root)), "wrong capture path")
    selection_file = cap / "selection.json"
    require(prepared["selection"] == str(selection_file.relative_to(root)), "wrong selection path")
    reports, obs, captured_cpu = profile(case, cap, probe_sha)
    if not hit:
        acct.launch(obs["pid"], METER)
    else:
        require(timing["children_cpu_ns"] == 0, "warm preparation charged child CPU")
    index, attempts = select("footprint", list(range(len(bank))), {i: p["reports"] for i, p in enumerate(bank)}, reports)
    if index is None:
        policy = prior.paid_policy(cap, obs, case, kernel)
        policy_file = cap / "policy.json"
        if not hit:
            acct.launch(policy["native_pid"], (case["program"],))
    else:
        policy = bank[index]["policy"]
        policy_file = bank[index]["cap"] / "policy.json"
        require(not (cap / "policy.json").exists(), "unexpected extra paid calibration")
    if fresh_path is None:
        path = cap / "selected-policy.json"
        require(path.read_bytes() == policy_file.read_bytes(), "cache entry selected a different policy")
        policy_file = path
    require(prepared["policy"] == str(policy_file.relative_to(root)), "wrong selected policy path")
    selection = dict(schema=1, method="footprint", action="calibrate" if index is None else "reuse",
                     reference_index=index, policy_sha256=policy["hash"], prediction_ns=prior.estimate(policy, obs),
                     comparisons=attempts, current_sample_sha256=obs["sample_sha256"])
    require(load(selection_file) == selection, "selection differs from independent frozen-bank replay")
    measured = dict(hit=hit, cache_entry_sha256=key, capture_pid=obs["pid"], action=selection["action"], policy_sha256=policy["hash"],
                    preflight_prediction_ns=selection["prediction_ns"], timing=timing,
                    profile_target_cpu_ns=0 if hit else captured_cpu,
                    native_target_cpu_ns=policy["native_ns"] if not hit and index is None else 0)
    # A child is reaped inside the measured interval. Linux's two separate
    # microsecond rusage readings can round a few microseconds differently.
    require(timing["children_cpu_ns"] + 4000 >= measured["profile_target_cpu_ns"] + measured["native_target_cpu_ns"],
            "child CPU interval omits a charged target")
    return dict(key=key, cap=cap, reports=reports, obs=obs, policy=policy, identity=identity, receipt=receipt, selection=selection,
                selection_sha=sha(selection_file.read_bytes())), measured


def totals(pairs):
    result = dict(pairs=len(pairs), equal_policy_pairs=0, equal_verdicts=0, limits=0, comparable_pairs=0)
    for mode in ("fresh", "session"):
        result[mode] = dict(preflight_cpu_ns=0, preflight_wall_ns=0, live_cpu_ns=0, live_wall_ns=0,
                            end_to_end_cpu_ns=0, end_to_end_wall_ns=0, profile_target_cpu_ns=0,
                            native_target_cpu_ns=0, profile_launches=0)
    for pair in pairs:
        left, right = pair["fresh"], pair["session"]
        same = left["policy_sha256"] == right["policy_sha256"]
        matches = [a["tle"] == b["tle"] for a, b in zip(left["live"], right["live"], strict=True)]
        result["equal_policy_pairs"] += same
        result["equal_verdicts"] += sum(matches)
        result["limits"] += len(matches)
        if not same or not all(matches):
            continue
        result["comparable_pairs"] += 1
        for mode in ("fresh", "session"):
            arm, out = pair[mode], result[mode]
            for stat in ("cpu_ns", "wall_ns"):
                prep = arm["timing"][stat]
                live_total = sum(r["timing"][stat] for r in arm["live"])
                out["preflight_" + stat] += prep
                out["live_" + stat] += live_total
                out["end_to_end_" + stat] += prep + live_total
            for stat in ("profile_target_cpu_ns", "native_target_cpu_ns"):
                out[stat] += arm[stat]
            out["profile_launches"] += not arm["hit"]
    return result


def verify(root, collection=None):
    if collection is None:
        collection = HERE.parent / "results/gce-preflight-cache/collection.json"
    manifest = load(collection)
    names = manifest["cache_implementation_files"]
    require(isinstance(names, list) and "preflight_cache/session.py" in names and len(names) == len(set(names)),
            "missing or duplicate registered implementation files")
    sources = manifest["input_sources_sha256"]
    hashes = {name: sources["experiments/" + name] for name in names}
    require(all(digest(value) for value in hashes.values()), "invalid implementation source digest")
    implementation = session.object_hash(hashes)
    plan_raw = (HERE / "plan.json").read_bytes()
    plan = json.loads(plan_raw)
    summary = load(root / "summary.json")
    require(summary["schema"] == 1 and summary["plan_sha256"] == sha(plan_raw)
            and summary["affinity"] == [1] and summary["cgroup_cpus"] == "1"
            and summary["probe_sha256"] == temporal.PROBE_SHA == plan["probe_sha256"]
            and uint(summary["steal_delta"]), "invalid registration or CPU placement")
    cases = {c["case_id"]: c for c in plan["cases"]}
    for c in cases.values():
        stdin = c["stdin"].encode()
        require((root / "inputs" / sha(stdin)).read_bytes() == stdin, "registered stdin bytes differ")
    reader = release_reader(load(anchor.PROTOCOL)["tools_sha256"]["production_reader.py"])
    acct = Accounting()
    probes = summary["probe_sha256"]
    kernel = summary["kernel"]
    seeds, seed_costs, seen = {}, {}, {}
    require(summary["seeds"].keys() == plan["anchors"].keys(), "different frozen bank")
    for name, case_id in plan["anchors"].items():
        row = summary["seeds"][name]
        require(row["case_id"] == case_id, "different seed")
        seeds[name], seed_costs[name] = check_prepared(root, row, cases[case_id], kernel, [], seen, acct, probes)

    host_identities = {tuple(seed["identity"][k] for k in ("session", "boot_sha256", "kernel", "cpu", "implementation_sha256"))
                       for seed in seeds.values()}
    require(len(host_identities) == 1 and all(seed["identity"]["implementation_sha256"] == implementation
            for seed in seeds.values()), "cache entries differ from the guest implementation or boot session")
    require(sources["experiments/preflight_cache/plan.json"] == summary["plan_sha256"], "plan differs from packaged source")
    queries = plan["queries"]
    rows = iter(summary["rows"])
    pairs = []
    for rnd in range(plan["rounds"]):
        for step in range(len(queries)):
            index = (step + rnd) % len(queries)
            query = queries[index]
            name = query["case_id"]
            case = cases[name]
            pair = dict(round=rnd, case_id=name)
            order = ("fresh", "session") if (rnd + index) % 2 == 0 else ("session", "fresh")
            for position, mode in enumerate(order):
                seq = 2 * (rnd * len(queries) + step) + position
                row = next(rows, None)
                require(row and row["sequence"] == seq and row["round"] == rnd and row["arm"] == mode
                        and row["case_id"] == name, "different trial schedule")
                folder = root / "trials" / f"{seq:03d}"
                context, measured = check_prepared(root, row, case, kernel, [seeds[case["program"]]], seen,
                                                   acct, probes, folder / "capture" if mode == "fresh" else None)
                measured["live"] = []
                for j, (slot, ms) in enumerate(zip(row["limits"], query["limits_ms"], strict=True)):
                    require(slot["limit_ms"] == ms, "different assigned deadline")
                    measured["live"].append(finish_live(folder / f"limit-{j}", slot, case, plan, context["policy"],
                                                        reader, acct, context["selection"], context["obs"], context["selection_sha"]))
                pair[mode] = measured
            pairs.append(pair)
    require(next(rows, None) is None, "unexpected extra trials")
    verify_placement(root, acct.pids)
    events, _ = parse_trace((root / "exec-trace").read_text())
    executables = set(METER) | set(plan["anchors"])
    measured_pids = {pid for pid, commands in events.items() if any(r["executable"] in executables for r in commands)}
    require(measured_pids == set(acct.pids), "unaccounted metered or native workload in the trace")
    cache = root / "cache"
    entry_sizes = {p.name: sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
                   for p in cache.iterdir() if p.is_dir()}
    require(set(entry_sizes) == set(seen.values()), "unexpected cache entries")
    metadata_bytes = sum(p.stat().st_size for p in cache.iterdir() if p.is_file())
    anchor_bytes = sum(entry_sizes[seed["key"]] for seed in seeds.values())
    storage = dict(anchor_bytes=anchor_bytes, query_bytes=sum(entry_sizes.values()) - anchor_bytes,
                   session_metadata_bytes=metadata_bytes, total_bytes=sum(entry_sizes.values()) + metadata_bytes,
                   entries=len(entry_sizes), entry_bytes=entry_sizes)
    groups = {name: totals([p for p in pairs if p["session"]["hit"] == warm])
              for name, warm in (("cold", False), ("warm", True))}
    return dict(schema=1, plan_sha256=sha(plan_raw), summary_sha256=sha((root / "summary.json").read_bytes()),
                verified_pids=len(acct.pids), implementation_sha256=implementation, cache_storage=storage, steal_ticks=summary["steal_delta"], anchors=seed_costs, totals=groups, pairs=pairs)


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("directory", type=Path)
    cli.add_argument("--output", type=Path)
    cli.add_argument("--collection", type=Path, help="immutable guest collection manifest")
    args = cli.parse_args()
    result = verify(args.directory, args.collection)
    if args.output:
        args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result["totals"], indent=2))
