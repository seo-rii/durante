"""Reconcile both registered live arms with the Aonohako Go reference command at every certified deadline."""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import sys
import tempfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from deadline_accuracy import verify
from shape_calibration import aonohako_clock


def project_limits(export, limits):
    """First try one certificate for the group, then retain separate rigorously certified limits."""
    try:
        receipt = export(limits)
        return [(limits, receipt)]
    except aonohako_clock.ProjectionUnavailable:
        if len(limits) == 1:
            return [(limits, None)]
    results = []
    for ms in limits:
        try:
            receipt = export([ms])
        except aonohako_clock.ProjectionUnavailable:
            receipt = None
        results.append(([ms], receipt))
    return results


def replay(root, binaries, reference, strategy="near-three", contest=False):
    primary = verify.verify(root, strategy, contest)
    summary = verify.load(root / "summary.json")
    candidate = "exact" if strategy == "exact-one" else "gate"
    counts = {arm: Counter() for arm in ("baseline", candidate)}
    uncovered = []
    groups = 0
    certificates = 0
    for row in summary["rows"]:
        if not row["limits"]:
            continue
        name = row["case_id"]
        elf = binaries / primary["cases"][name]["program"]
        capture = root / "cases" / name
        stdin = capture / "full/stdin"
        by_binding = defaultdict(list)
        for event in row["limits"]:
            by_binding[(event["selection_path"], event["policy_path"])].append(event)
        for (selection_path, policy_path), events in by_binding.items():
            groups += 1
            limits = sorted({e["limit_ms"] for e in events})
            def export(deadlines):
                return aonohako_clock.export(root / selection_path, capture, root / policy_path, elf, stdin, deadlines)
            with tempfile.TemporaryDirectory() as tmp:
                for covered, receipt in project_limits(export, limits):
                    path = Path(tmp) / "projection.json"
                    if receipt is not None:
                        path.write_text(json.dumps(receipt))
                        certificates += 1
                    for event in events:
                        ms, arm = event["limit_ms"], event["method"]
                        if ms not in covered:
                            continue
                        wrapped = capture / "limits" / arm / f"{event['index']:02d}" / "result.json"
                        if receipt is None:
                            counts[arm]["unavailable"] += 1
                            uncovered.append(dict(case_id=name, arm=arm, limit_ms=ms, policy=policy_path))
                            continue
                        status = aonohako_clock.replay_live(reference, path, wrapped, elf, stdin, ms)
                        counts[arm][status] += 1
    result = dict(schema=1, authority="diagnostic_only", summary_sha256=primary["summary_sha256"],
                  bindings=groups, certified_projections=certificates, arms=counts, unavailable=uncovered)
    if strategy == "exact-one":
        result["strategy"] = strategy
    return result


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("directory", type=Path)
    cli.add_argument("--binaries", type=Path, required=True)
    cli.add_argument("--reference", type=Path, required=True)
    cli.add_argument("--output", type=Path)
    cli.add_argument("--strategy", choices=("near-three", "exact-one"), default="near-three")
    cli.add_argument("--contest", action="store_true", help="use the contest exact-one registration")
    args = cli.parse_args()
    result = replay(args.directory, args.binaries, args.reference.resolve(), args.strategy, args.contest)
    output = json.dumps(result, indent=2)
    if args.output:
        args.output.write_text(output + "\n")
    print(output)
    raise SystemExit(1 if result["unavailable"] else 0)
