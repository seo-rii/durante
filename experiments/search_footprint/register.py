"""Freeze this independent capacity comparison, including its predecessor and shared collector."""

import argparse
import hashlib
import json
from pathlib import Path

from study import BASE, CASES, HERE, PLAN, ROOT


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def registration(probe):
    prior_plan = BASE / "protocol.json"
    previous = json.loads(prior_plan.read_bytes())
    previous_data = ROOT / "experiments/results/gce-search-order"
    fixtures = ROOT / "experiments/results/gce-search-footprint/fixtures.json"
    cases = json.loads(fixtures.read_bytes())["cases"]
    if tuple(c["workload_id"] for c in cases) != CASES:
        raise ValueError("unexpected comparison input order")
    sources = dict(previous["sources_sha256"])
    if any(digest(ROOT / path) != expected for path, expected in sources.items()):
        raise ValueError("source of the earlier acquisition changed")
    paths = [
        fixtures,
        prior_plan,
        previous_data / "platform.json",
        previous_data / "evaluation.json",
        previous_data / "dataset.json.gz",
        previous_data / "receipts.tar.xz",
    ]
    paths.extend(
        p
        for p in HERE.rglob("*")
        if p.is_file()
        and p != PLAN
        and p.suffix in {".py", ".h", ".c", ".S", ".txt", ".json", ".patch"}
    )
    sources.update((str(p.relative_to(ROOT)), digest(p)) for p in paths)
    tools = dict(previous["tool_sha256"], search_probe=digest(probe))
    if tools["search_probe"] == previous["tool_sha256"]["search_probe"]:
        raise ValueError("expected independently built extrema sampler")
    archives = {case["binary_archive"] for case in cases}
    return {
        "schema": 1,
        "probe_schema": 2,
        "rounds": 4,
        "metered_phases": ["fast", "sampled"],
        "benchmark_cpu": 1,
        "observer_cpus": [0],
        "image_manifest_sha256": previous["image_manifest_sha256"],
        "tool_sha256": tools,
        "previous_protocol_sha256": digest(prior_plan),
        "design": "Run all twelve inputs in four native-bracketed quartets. Reverse input order in rounds two "
        "and four and rotate the twelve positions by six in rounds three and four. Alternate the "
        "position of the v9001-only and v9001-plus-extrema measurements for each input. Reopen "
        "the registered stdin file for each child and hash all files before and after collection.",
        "sources_sha256": dict(sorted(sources.items())),
        "archives_sha256": {path: digest(ROOT / path) for path in sorted(archives)},
        "cases": cases,
    }


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("probe", type=Path)
    cli.add_argument("--write", action="store_true")
    args = cli.parse_args()
    result = registration(args.probe)
    if args.write:
        PLAN.write_text(json.dumps(result, indent=2) + "\n")
    elif json.loads(PLAN.read_bytes()) != result:
        raise ValueError("registered source or binary hashes differ")
    print(digest(PLAN))
