"""Replay both workload and input-probe receipts and compare paired deadline errors."""
from fractions import Fraction
from pathlib import Path
from statistics import median
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from contest_branch.verify import main, verify_study
from wall_locality import bench, corpus, pages, score


def verify(root):
    report = verify_study(root, HERE, corpus, score, bench.PROFILE_ARMS,
                          read_probe=bench.read_probe, reference_counts=pages.reference_counts)
    ratios = {"joint_over_full": [], "joint_over_ordinary": [], "probe_over_native": []}
    changes = {"false_pass_fixed": [], "false_pass_added": [], "false_timeout_fixed": [], "false_timeout_added": []}
    for name, case in report["cases"].items():
        times = {arm: median(Fraction(o["cpu_us"]) for o in obs) for arm, obs in case["profiles"].items()}
        combined = times["branch_only"] + times["leaf_pages"]
        ratios["joint_over_full"].append(combined / times["full"])
        ratios["joint_over_ordinary"].append(combined / times["ordinary"])
        ratios["probe_over_native"].append(times["leaf_pages"] * 1000 / median(case["native_ns"]))
        if "predictions" not in case:
            continue
        for threshold, votes in case["native_tle_votes"].items():
            trials = len(case["native_ns"])
            if votes not in (0, trials):
                continue
            limit = int(threshold) * 1_000_000
            before = case["predictions"]["branch_misses"] > limit
            after = case["predictions"]["branch_pages"] > limit
            if before != after:
                kind = "false_pass" if votes == trials else "false_timeout"
                change = "fixed" if after == bool(votes) else "added"
                changes[kind + "_" + change].append([name, int(threshold)])
    for key, values in ratios.items():
        report["profile_cost"]["median_" + key + "_ppm"] = round(1_000_000 * median(values))
    held = [c for c in report["cases"].values() if "predictions" in c]
    worst = {method: max(abs(Fraction(c["predictions"][method], median(c["native_ns"])) - 1) for c in held)
             for method in ("branch_misses", "branch_pages")}
    report["unanimous_changes"] = changes
    report["qualification"] = {
        "lower_worst_error": worst["branch_pages"] < worst["branch_misses"],
        "fewer_false_passes_without_new_false_timeouts":
            len(changes["false_pass_fixed"]) > len(changes["false_pass_added"]) and not changes["false_timeout_added"],
    }
    return report


if __name__ == "__main__":
    main(verify)
