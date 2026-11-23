"""Score the three-sample deadline flag against independent native votes and actual timeouts."""
import argparse
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from contest_batch.verify import verify
from cpp20_locality.verify import require, sha


def tally(trials, *, fixed):
    counts = dict(limits=0, flagged_mixed=0, flagged_unanimous=0, unflagged_mixed=0, unflagged_unanimous=0,
                  majority_misses_flagged=0, majority_misses_unflagged=0)
    for trial in trials:
        references = len(trial["native_ns"])
        for point in trial["deadlines"]:
            if fixed and not point["fixed"]:
                continue
            flagged = point["calibration_window"]["split"]
            votes = point["native_tle_reps"]
            mixed = 0 < votes < references
            counts["limits"] += 1
            group = "flagged" if flagged else "unflagged"
            counts[group + ("_mixed" if mixed else "_unanimous")] += 1
            if point["warm"]["tle"] != (2 * votes > references):
                counts["majority_misses_" + group] += 1
    return counts


def report(directory):
    result = verify(directory)
    require(result["plan_sha256"] == sha((HERE / "plan.json").read_bytes()), "expected contest scale registration")
    cohorts = {}
    for name in ("prospective", "previous"):
        trials = [c for c in result["cases"].values() if c["cohort"] == name]
        cohorts[name] = dict(all=tally(trials, fixed=False), fixed=tally(trials, fixed=True))
    return dict(schema=1, authority="diagnostic_only", summary_sha256=result["summary_sha256"], cohorts=cohorts)


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("directory", type=Path)
    cli.add_argument("--output", type=Path)
    args = cli.parse_args()
    output = json.dumps(report(args.directory), indent=2)
    if args.output:
        args.output.write_text(output + "\n")
    print(output)
