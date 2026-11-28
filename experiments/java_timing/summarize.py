#!/usr/bin/env python3
"""Summarize experiments/java_timing/results.json.

  python3 -B experiments/java_timing/summarize.py results.json [--write summary.json] [--markdown]

Every sample must have exited 0 with the reference output, and every Durante
sample must report a valid measurement. For each profile, configuration, mode
and problem the summary takes the minimum and the median CPU time (user + system
of the whole tree) over the rounds; each is divided by the same statistic of the
native runs of that profile and problem, and the ratios are combined by a
geometric mean over the problems. The minimum is the statistic of the corpus
table in docs/CONTEST_CORPUS.md.
"""
import argparse
import json
import math
import statistics
import sys
from pathlib import Path


def geomean(values):
    return math.exp(sum(math.log(v) for v in values) / len(values))


def summarize(data):
    samples = data["samples"]
    bad = [s for s in samples if s["exit"] != 0 or not s["output_ok"] or
           (s["mode"] != "native" and s.get("valid") is not True)]
    if bad:
        raise SystemExit(f"{len(bad)} failed samples, first: {bad[0]}")
    cpu = {}
    for s in samples:
        cpu.setdefault((s["lang"], s["config"], s["mode"], s["problem"]), []).append(s["cpu_s"])
    problems = sorted({s["problem"] for s in samples})
    summary = {"rounds": data["provenance"]["rounds"], "problems": problems, "profiles": {}}
    for lang, configs in data["langs"].items():
        native = {p: cpu[(lang, "native", "native", p)] for p in problems}
        profile = {"native_cpu_s": {p: {"min": min(v), "median": statistics.median(v)}
                                    for p, v in native.items()}, "configs": {}}
        for config in configs:
            entry = {}
            for mode in ("durante", "budget-large"):
                per = {}
                for p in problems:
                    v = cpu[(lang, config, mode, p)]
                    per[p] = {"min": min(v), "median": statistics.median(v),
                              "ratio_min": min(v) / min(native[p]),
                              "ratio_median": statistics.median(v) / statistics.median(native[p])}
                entry[mode] = {"per_problem": per,
                               "geomean_ratio_min": geomean([per[p]["ratio_min"] for p in problems]),
                               "geomean_ratio_median": geomean([per[p]["ratio_median"] for p in problems]),
                               "geomean_cpu_s_min": geomean([per[p]["min"] for p in problems]),
                               "geomean_cpu_s_median": geomean([per[p]["median"] for p in problems])}
            profile["configs"][config] = entry
        summary["profiles"][lang] = profile
    return summary


def rounded(value):
    """Round floats so that the committed summary is stable text."""
    if isinstance(value, float):
        return round(value, 4)
    if isinstance(value, dict):
        return {k: rounded(v) for k, v in value.items()}
    if isinstance(value, list):
        return [rounded(v) for v in value]
    return value


def markdown(summary):
    lines = []
    for lang, profile in summary["profiles"].items():
        configs = list(profile["configs"])
        lines.append(f"### `{lang}`, minimum of {summary['rounds']} rounds (x native)\n")
        lines.append("| Problem | native (s) | " + " | ".join(f"{c} plain | {c} budget" for c in configs) + " |")
        lines.append("|---|---:|" + "---:|---:|" * len(configs))
        for p in summary["problems"]:
            row = [p, f"{profile['native_cpu_s'][p]['min']:.3f}"]
            for c in configs:
                for mode in ("durante", "budget-large"):
                    row.append(f"{profile['configs'][c][mode]['per_problem'][p]['ratio_min']:.1f}")
            lines.append("| " + " | ".join(row) + " |")
        row = ["geometric mean", ""]
        for c in configs:
            for mode in ("durante", "budget-large"):
                row.append(f"**{profile['configs'][c][mode]['geomean_ratio_min']:.1f}**")
        lines.append("| " + " | ".join(row) + " |\n")
        lines.append(f"Geometric means of `{lang}` CPU seconds (minimum / median of rounds):\n")
        lines.append("| Configuration | plain | budget-large |")
        lines.append("|---|---:|---:|")
        for c in configs:
            d, b = profile["configs"][c]["durante"], profile["configs"][c]["budget-large"]
            lines.append(f"| {c} | {d['geomean_cpu_s_min']:.2f} / {d['geomean_cpu_s_median']:.2f} | "
                         f"{b['geomean_cpu_s_min']:.2f} / {b['geomean_cpu_s_median']:.2f} |")
        lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("results", type=Path)
    parser.add_argument("--write", type=Path)
    parser.add_argument("--markdown", action="store_true")
    args = parser.parse_args()
    summary = rounded(summarize(json.loads(args.results.read_text())))
    if args.write:
        args.write.write_text(json.dumps(summary, indent=1) + "\n")
    if args.markdown:
        print(markdown(summary))
    else:
        json.dump(summary, sys.stdout, indent=1)
        print()


if __name__ == "__main__":
    main()
