#!/usr/bin/env python3
"""Summarize ordinary-live CPU and wall time in pairs of opposite-order rounds."""
import argparse
from collections import Counter
import json
from math import expm1, isfinite, log
from pathlib import Path
from statistics import median

from reference_features import require

ARMS = ("baseline", "candidate")


def effect(rounds, metric):
    blocks = []
    for index in range(0, len(rounds), 2):
        first, second = rounds[index:index + 2]
        require(first["first_arm"] != second["first_arm"], "expected opposite arm orders in a block")
        ratios = [log(r[metric][1] / r[metric][0]) for r in (first, second)]
        blocks.append(sum(ratios) / 2)
    return {
        "median_change_percent": 100 * expm1(median(blocks)),
        "faster_blocks": sum(value < 0 for value in blocks),
        "blocks": len(blocks),
    }


def summarize(report):
    count = report["rounds"]
    require(report["mode"] == "ordinary-live" and set(report["clients"]) == set(ARMS),
            "two ordinary live clients required")
    require(type(count) is int and count >= 2 and count % 2 == 0, "expected an even number of rounds")
    samples = report["samples"]
    counts = Counter(sample["name"] for sample in samples)
    require(counts and set(counts) == set(report["cases"]) and all(n == 2 * count for n in counts.values()),
            "missing or unexpected observations")
    cases = {}
    for name, identity in report["cases"].items():
        rows = [row for row in samples if row["name"] == name]
        work = rows[0]["work"]
        require(work["valid"] and work["quality"] == "uninterrupted" and work["app_exit_seen"] and
                work["app_exit_code"] == 0 and not work["accounting_error"] and not work["signals_observed"] and
                work["threads_complete"] and work["threads_started"] == work["threads_finished"] == 1,
                f"{name}: incomplete work")
        rounds = []
        for index in range(count):
            pair = rows[2 * index:2 * (index + 1)]
            by_arm = {row["mode"]: row for row in pair}
            require(set(by_arm) == set(ARMS) and all(row["round"] == index + 1 for row in pair),
                    f"{name}: invalid arm or round")
            require(all(row["cache"] is None and row["work"] == work for row in pair),
                    f"{name}: unexpected probe or unequal work")
            times = {"first_arm": pair[0]["mode"]}
            for clock in ("cpu", "wall"):
                values = [by_arm[arm][clock + "_s"] for arm in ARMS]
                require(all(isfinite(s) and s > 0 for s in values), f"{name}: invalid {clock} time")
                ns = [round(s * 1e9) for s in values]
                require(min(ns) > 0, f"{name}: zero {clock} time")
                times[clock + "_ns"] = ns
            rounds.append(times)
        cases[name] = {**identity, "work": work, "rounds": rounds,
                       "cpu": effect(rounds, "cpu_ns"), "wall": effect(rounds, "wall_ns")}
    return {"schema": 1, "time_columns": list(ARMS), "clients": report["clients"],
            "launcher_sha256": report["drrun_sha256"], "cpu": report["cpu"],
            "rounds_per_case": count, "cases": cases}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("summary", type=Path, help="summary.json from compare.py --ordinary-live")
    args = parser.parse_args()
    try:
        print(json.dumps(summarize(json.loads(args.summary.read_bytes())), indent=2, allow_nan=False))
    except (KeyError, TypeError, ValueError) as error:
        parser.error(str(error))
