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
import re
import statistics
import sys
from pathlib import Path


def positive_finite(value):
    if type(value) not in (int, float) or value <= 0:
        return False
    try:
        return math.isfinite(value)
    except OverflowError:  # A JSON integer too large for a floating-point timing.
        return False


def geomean(values):
    if not all(positive_finite(v) for v in values):
        raise ValueError("nonfinite or nonpositive aggregate timing")
    return math.exp(sum(math.log(v) for v in values) / len(values))


def validate_profiles(langs, configs):
    if not isinstance(langs, dict) or not langs or not isinstance(configs, dict) or not configs:
        raise ValueError("expected nonempty profile and configuration declarations")
    for lang, names in langs.items():
        if lang not in ("java", "java-cds") or not isinstance(names, list) or not names:
            raise ValueError(f"invalid Java profile declaration: {lang!r}")
        if any(not isinstance(n, str) or not n or n not in configs for n in names):
            raise ValueError(f"unknown configuration for {lang!r}")
        if len(set(names)) != len(names):
            raise ValueError(f"duplicate configuration for {lang!r}")


def summarize(data):
    if not isinstance(data, dict) or type(data.get("schema")) is not int or data["schema"] != 1:
        raise ValueError("expected Java timing schema 1")
    provenance = data["provenance"]
    rounds = provenance["rounds"]
    if type(rounds) is not int or rounds <= 0:
        raise ValueError("rounds must be a positive integer")
    inputs = provenance["input_sha256"]
    if not isinstance(inputs, dict) or not inputs or any(
            not p or not isinstance(h, str) or not re.fullmatch(r"[0-9a-f]{64}", h) for p,h in inputs.items()):
        raise ValueError("expected declared problems with SHA-256 input hashes")
    langs = data["langs"]
    validate_profiles(langs, provenance["configs"])
    problems = sorted(inputs)
    cpu = {}
    for problem in problems:
        for lang, names in langs.items():
            cpu[lang, "native", "native", problem] = {}
            for config in names:
                for mode in ("durante", "budget-large"):
                    cpu[lang, config, mode, problem] = {}
    if not isinstance(data["samples"], list):
        raise ValueError("expected a list of samples")
    for s in data["samples"]:
        key = (s["lang"], s["config"], s["mode"], s["problem"])
        if key not in cpu:
            raise ValueError(f"undeclared sample: {key}")
        index = s["round"]
        if type(index) is not int or not 0 <= index < rounds:
            raise ValueError(f"invalid round {index!r} for {key}")
        if index in cpu[key]:
            raise ValueError(f"duplicate sample for {key} in round {index}")
        if (type(s["exit"]) is not int or s["exit"] != 0 or s["output_ok"] is not True
                or (s["mode"] != "native" and s.get("valid") is not True)):
            raise ValueError(f"failed sample for {key} in round {index}")
        if not positive_finite(s["cpu_s"]) or not positive_finite(s["wall_s"]):
            raise ValueError(f"invalid CPU or wall time for {key} in round {index}")
        cpu[key][index] = s["cpu_s"]
    for key, samples in cpu.items():
        if len(samples) != rounds:
            missing = next(i for i in range(rounds) if i not in samples)
            raise ValueError(f"missing sample for {key} in round {missing}")
    cpu = {key: samples.values() for key, samples in cpu.items()}
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
    try:
        summary = rounded(summarize(json.loads(args.results.read_text())))
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        parser.error(f"invalid timing data: {error}")
    if args.write:
        args.write.write_text(json.dumps(summary, indent=1) + "\n")
    if args.markdown:
        print(markdown(summary))
    else:
        json.dump(summary, sys.stdout, indent=1)
        print()


if __name__ == "__main__":
    main()
