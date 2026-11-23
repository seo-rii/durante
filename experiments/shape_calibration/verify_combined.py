"""Replay paired one-pass and two-pass profile captures and selections against a frozen prior anchor bank."""
import argparse
from collections import defaultdict
from fractions import Fraction
import json
from pathlib import Path, PurePosixPath
import shutil
from statistics import median
import sys
import tarfile
import tempfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from calibrated_limits.verify import METER, load, positive, verify_placement, wait4_ns
from cpp20_locality.verify import require, sha
from guarded_native.score import MS
from shape_calibration import shape, temporal
from shape_calibration import verify as original

PRIOR_ARCHIVE = HERE.parent / "results/gce-shape-calibration/output.tar.xz"
ARMS = ("separate", "combined")


def unpack(archive, parent):
    with tarfile.open(archive, "r:xz") as source:
        for member in source:
            path = PurePosixPath(member.name)
            require(member.isfile() and path.parts and path.parts[0] == "output" and ".." not in path.parts,
                    "unexpected member in raw receipts archive")
            dest = parent / path
            dest.parent.mkdir(parents=True, exist_ok=True)
            with source.extractfile(member) as stream, dest.open("xb") as out:
                shutil.copyfileobj(stream, out)
    return parent / "output"


def frozen_bank(cases, prior_digest):
    require(sha(PRIOR_ARCHIVE.read_bytes()) == prior_digest, "different original shape study archive")
    banks = defaultdict(list)
    snapshots = {}
    all_anchors = {}
    with tempfile.TemporaryDirectory() as tmp:
        root = unpack(PRIOR_ARCHIVE, Path(tmp))
        previous = original.verify(root)
        summary = load(root / "summary.json")
        for case, row in zip(cases, summary["rows"], strict=True):
            name, program = case["case_id"], case["program"]
            directory = root / "cases" / name
            selected = row["policies"]["temporal"]
            snapshots[name] = {"prior_names": banks[program].copy(), **selected}
            if (directory / "policy.json").is_file():
                profile = original.check_full(case, directory / "full")
                extra = temporal.inspect_run(directory / "temporal", case["binary_sha256"], sha(case["stdin"].encode()),
                                             sha(case["stdout"].encode()), previous["probe_sha256"])
                policy = original.paid_policy(directory, profile["observation"], case, summary["kernel"])
                all_anchors[name] = {"profile": profile, "temporal": extra, "policy": policy}
            if selected["action"] == "calibrate":
                banks[program].append(name)
    return snapshots, all_anchors


def selected_clock(case, current, reference, anchors):
    name = case["case_id"]
    index, details = original.bank_decision("temporal", reference["prior_names"], anchors, current)
    selected_name = reference["prior_names"][index] if index is not None else name
    action = "reuse" if index is not None else "calibrate"
    policy = anchors.get(selected_name)
    prediction = original.estimate(policy["policy"], current["profile"]["observation"]) if policy else None
    return {"action": action, "anchor_case": selected_name, "covered": policy is not None,
            "prediction_ns": prediction,
            "admission": [{"anchor_case": n, "shape_reasons": d["shape"]["reasons"], "temporal_reasons": d["temporal"]["reasons"]}
                          for n, d in zip(reference["prior_names"][:len(details)], details, strict=True)],
            "implied_tle": [(prediction > limit * MS if prediction is not None else None) for limit in case["limits_ms"]]}


def capture_receipts(directory, arm, row, case, probe_sha):
    from shape_calibration import combined

    obs = original.check_full(case, directory / "full")
    args = (case["binary_sha256"], sha(case["stdin"].encode()), sha(case["stdout"].encode()), probe_sha)
    if arm == "combined":
        extra = combined.inspect_temporal(directory, *args)
    else:
        require(not (directory / "combined.json").exists(), "separate capture has combined marker")
        extra = temporal.inspect_run(directory / "temporal", *args)
    cpu_spatial = original.profile_cpu_ns(directory / "full")
    cpu_temporal = wait4_ns(extra["observation"])
    pids = [obs["observation"]["pid"], extra["observation"]["pid"]]
    require((pids[0] == pids[1]) == (arm == "combined") and pids == [row["profile_pid"], row["temporal_pid"]],
            "physical process attribution differs from requested capture")
    require(row["profile_cpu_ns"] == cpu_spatial and row["temporal_cpu_ns"] == cpu_temporal,
            "different target CPU in profile evidence")
    if arm == "combined":
        require(cpu_temporal == cpu_spatial, "combined samplers disagree on the same wait4 charge")
        cpu = cpu_spatial
    else:
        cpu = cpu_spatial + cpu_temporal
    require(row["target_cpu_ns"] == cpu, "incorrect number of wait4 charges")
    return {"profile": obs, "temporal": extra, "cpu_ns": cpu, "pids": list(dict.fromkeys(pids))}


def load_deltas(left, right):
    rows = [{s["offset"]: s["loads"] for s in profile["sites"]} for profile in (left, right)]
    totals = [sum(r.values()) for r in rows]
    offsets = rows[0].keys() | rows[1].keys()
    counts = [abs(rows[0].get(k, 0) - rows[1].get(k, 0)) for k in offsets]
    return {"loads": totals, "sites": [len(r) for r in rows], "total_delta": abs(totals[1] - totals[0]),
            "largest_site_delta": max(counts, default=0), "sites_with_different_loads": sum(c != 0 for c in counts),
            "sum_absolute_site_deltas": sum(counts)}


def verdict_comparison(left, right):
    pairs = list(zip(left, right, strict=True))
    return {"decisions": len(pairs), "uncovered": sum(a is None or b is None for a, b in pairs),
            "different": sum(a is not None and b is not None and a != b for a, b in pairs)}


def score_pair(case, row, captures, reference, anchors):
    selected = {arm: selected_clock(case, captures[arm], reference, anchors) for arm in ARMS}
    old_tle = [reference["prediction_ns"] > ms * MS for ms in case["limits_ms"]]
    left, right = (captures[k] for k in ARMS)
    separate, fused = (selected[k] for k in ARMS)
    spatial_cmp = shape.compare(left["profile"], right["profile"])
    temporal_cmp = temporal.compare(left["temporal"], right["temporal"])
    def extrema(sites, field):
        values = [Fraction(s[field]) for s in sites if s.get(field) is not None]
        return str(max(values)) if values else None
    delta = left["cpu_ns"] - right["cpu_ns"]
    return {
        "sequence": row["sequence"], "round": row["round"], "order": row["order"], "case_id": case["case_id"],
        "cohort": case["cohort"], "split": case["split"], "limits_ms": case["limits_ms"],
        "separate_cpu_ns": left["cpu_ns"], "combined_cpu_ns": right["cpu_ns"], "saved_cpu_ns": delta,
        "saving_ppm": round(Fraction(delta, left["cpu_ns"]) * MS),
        "eligible_loads": {"spatial": load_deltas(left["profile"], right["profile"]),
                           "temporal": load_deltas(left["temporal"], right["temporal"]),
                           "inside_combined": load_deltas(right["profile"], right["temporal"])},
        "material_profile_comparison": {
            "spatial": spatial_cmp["reasons"], "temporal": temporal_cmp["reasons"],
            "max_far_fraction_delta": extrema(spatial_cmp["material_sites"], "far_change"),
            "max_span_ratio": extrema(spatial_cmp["material_sites"], "span_ratio"),
            "max_exact_fraction_delta": extrema(temporal_cmp["material_sites"], "exact_hits_difference"),
            "max_radius_fraction_delta": extrema(temporal_cmp["material_sites"], "radius_hits_difference"),
        },
        "prior": {"action": reference["action"], "anchor_case": reference["anchor_case"],
                  "prediction_ns": reference["prediction_ns"], "implied_tle": old_tle},
        "policies": selected,
        "cutoff_comparisons": {"paired": verdict_comparison(separate["implied_tle"], fused["implied_tle"]),
                               **{a + "_to_prior": verdict_comparison(selected[a]["implied_tle"], old_tle) for a in ARMS}},
    }


def aggregate(rows):
    separate = sum(r["separate_cpu_ns"] for r in rows)
    combined = sum(r["combined_cpu_ns"] for r in rows)
    def incompatible(a, b):
        return a["action"] != b["action"] or a["anchor_case"] != b["anchor_case"]
    cutoff = {comparison: {key: sum(r["cutoff_comparisons"][comparison][key] for r in rows)
                           for key in ("decisions","different","uncovered")}
              for comparison in ("paired", "separate_to_prior", "combined_to_prior")}
    return {"pairs": len(rows), "case_count": len({r["case_id"] for r in rows}),
            "distinct_limits": len({(r["case_id"], ms) for r in rows for ms in r["limits_ms"]}),
            "separate_cpu_ns": separate, "combined_cpu_ns": combined, "saved_cpu_ns": separate - combined,
            "aggregate_saving_ppm": round(Fraction(separate - combined, separate) * MS),
            "pairs_faster": sum(r["saved_cpu_ns"] > 0 for r in rows),
            "min_saving_ppm": min(r["saving_ppm"] for r in rows),
            "median_saving_ppm": median(r["saving_ppm"] for r in rows),
            "max_saving_ppm": max(r["saving_ppm"] for r in rows),
            "paired_selections_different": sum(incompatible(r["policies"]["separate"], r["policies"]["combined"]) for r in rows),
            "selections_different_from_prior": {a: sum(incompatible(r["policies"][a], r["prior"]) for r in rows) for a in ARMS},
            "spatial_material_rejections": sum(bool(r["material_profile_comparison"]["spatial"]) for r in rows),
            "temporal_material_rejections": sum(bool(r["material_profile_comparison"]["temporal"]) for r in rows),
            "max_eligible_load_delta": {item: max(r["eligible_loads"][item]["total_delta"] for r in rows)
                                        for item in ("spatial", "temporal", "inside_combined")},
            "max_site_load_delta": {item: max(r["eligible_loads"][item]["largest_site_delta"] for r in rows)
                                    for item in ("spatial", "temporal", "inside_combined")},
            "cutoffs": cutoff}


def verify(root):
    plan_bytes = (HERE / "combined-plan.json").read_bytes()
    plan = json.loads(plan_bytes)
    corpus = load(HERE / "plan.json")
    summary = load(root / "summary.json")
    require(plan["schema"] == summary["schema"] == 1 and plan["repetitions"] == 3
            and plan["algorithms"] == list(ARMS) and summary["combined_plan_sha256"] == sha(plan_bytes)
            and sha((HERE / "plan.json").read_bytes()) == summary["plan_sha256"] == plan["corpus_sha256"]
            and sha((HERE / "rules.json").read_bytes()) == summary["rules_sha256"] == plan["rules_sha256"]
            and summary["probe_sha256"] == load(HERE / "rules.json")["probe_sha256"] == temporal.PROBE_SHA,
            "different combined study, corpus, rules or temporal probe")
    require(summary["affinity"] == [1] and summary["cgroup_cpus"] == "1" and isinstance(summary["kernel"], str)
            and summary["kernel"] and type(summary["steal_delta"]) is int and summary["steal_delta"] >= 0,
            "missing CPU-pinned placement or steal counter")
    cases = corpus["cases"]
    by_name = {c["case_id"]: c for c in cases}
    require(len(by_name) == len(cases) == 13, "different paired case population")
    frozen, anchors = frozen_bank(cases, plan["reference_archive_sha256"])
    names = sorted(by_name, key=lambda n: sha(n.encode()))
    for case in cases:
        raw_input = case["stdin"].encode()
        require((root / "inputs" / sha(raw_input)).read_bytes() == raw_input, "registered input differs")
    schedule = []
    for rep in range(plan["repetitions"]):
        for name in names[rep:] + names[:rep]:
            schedule.append((rep, names.index(name), name))
    pids = {}
    pairs = []
    for seq, (row, (rep, index, name)) in enumerate(zip(summary["rows"], schedule, strict=True)):
        order = list(ARMS[::-1] if (rep + index) % 2 else ARMS)
        require(row["sequence"] == seq and row["round"] == rep + 1 and row["case_id"] == name
                and row["order"] == order and set(row["arms"]) == set(order), "wrong rotated paired schedule")
        case = by_name[name]
        captures = {}
        for arm in order:
            directory = root / "runs" / f"{seq:03d}" / arm
            capture = capture_receipts(directory, arm, row["arms"][arm], case, summary["probe_sha256"])
            for pid in capture["pids"]:
                require(positive(pid) and pid not in pids, "duplicate or missing physical profiler process")
                pids[pid] = METER
            captures[arm] = capture
        pairs.append(score_pair(case, row, captures, frozen[name], anchors))
    require(len(pids) == 3 * len(pairs) == 117, "unexpected profiler or run count")
    verify_placement(root, pids)
    totals = {"all": aggregate(pairs), "holdout": aggregate([r for r in pairs if r["split"] == "holdout"])}
    for group in ("calibration", "previous", "prospective"):
        totals[group] = aggregate([r for r in pairs if r["cohort"] == group])
    by_case = {name: aggregate([r for r in pairs if r["case_id"] == name]) for name in by_name}
    return {"schema": 1, "combined_plan_sha256": sha(plan_bytes), "corpus_sha256": plan["corpus_sha256"],
            "reference_archive_sha256": plan["reference_archive_sha256"], "summary_sha256": sha((root / "summary.json").read_bytes()),
            "pids_verified": len(pids), "steal_ticks": summary["steal_delta"], "cpu": 1,
            "totals": totals, "cases": by_case, "pairs": pairs}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="extracted fresh paired capture")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    measurements = verify(args.directory)
    if args.output:
        args.output.write_text(json.dumps(measurements, indent=2) + "\n")
    print(json.dumps(measurements["totals"], indent=2))
