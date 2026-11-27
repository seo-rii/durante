"""Recompute the post-hoc weight selection and grid scores from all four guest archives."""
from collections import Counter, defaultdict
from fractions import Fraction
import json
from math import ceil
from pathlib import Path
from statistics import median
import sys
import tarfile
import unittest

EXPERIMENTS = Path(__file__).resolve().parents[1] / "experiments"
sys.path.insert(0, str(EXPERIMENTS))
from scrivener_lazy.midpoint_comparison import compare


def extract(study, suffix, *, diagnostics=False):
    plan = json.loads((EXPERIMENTS / ("scrivener_" + study) / "plan.json").read_bytes())
    archive = EXPERIMENTS / "results" / ("gce-scrivener-" + study + suffix) / "output.tar.gz"
    with tarfile.open(archive) as source:
        run = json.load(source.extractfile("output/run.json"))
    times, features = defaultdict(list), {}
    for r in run["rows"]:
        name, program = r["name"], r["program"]
        if r["arm"] == "native":
            times[name, program].append((r["user_us"] + r["sys_us"]) * 1000)
        elif r["rep"] == 1 and r.get("lane") != "lazy" and r["arm"] in ("scan", "branch"):
            field = "disruptive" if r["arm"] == "scan" else "bcm"
            features[name, program, field] = r["features"][field]
    pairs = {}
    groups = {"anchor", "control"} if diagnostics else {"holdout"}
    for case in plan["cases"]:
        if case["group"] not in groups:
            continue
        name = case["name"]
        for program in plan["binaries"]:
            low, high = (median(times[n, program]) for n in plan["anchors"])
            normalized = []
            for field in ("disruptive", "bcm"):
                scanner = field == "disruptive"
                anchor_owner = "scan" if scanner else program
                case_owner = "scan" if scanner and study == "streams" else program
                a, b = (features[n, anchor_owner, field] for n in plan["anchors"])
                value = features[name, case_owner, field]
                ratio = Fraction(1, 2) if a == b else Fraction(value - a, b - a)
                normalized.append(min(1, max(0, ratio)))
            stream = ceil(low + Fraction(high - low, 2) * sum(normalized))
            pairs[name, program] = (ceil(Fraction(low + high, 2)), stream, times[name, program])
    return run["placement"]["boot_sha256"], pairs if diagnostics else list(pairs.values())


def observations(pairs, k):
    for middle, stream, native in pairs:
        estimated = ceil(middle + Fraction(k, 100) * (stream - middle))
        yield estimated, abs(Fraction(estimated, median(native)) - 1), native


class ScrivenerMidpointComparison(unittest.TestCase):
    def test_prior_only_selection_and_both_evaluation_archives(self):
        report = compare()
        training = [extract("streams", suffix) for suffix in ("", "-repeat")]
        pairs = [p for _, group in training for p in group]
        scores = []
        for k in range(101):
            rates = [r for _, r, _ in observations(pairs, k)]
            scores.append((max(rates), median(rates), k))
        worst, typical, selected = min(scores)
        self.assertEqual(report["training"], dict(study="scrivener_streams", boot_sha256=[boot for boot, _ in training],
                         observations=24, median_absolute_error_ppm=round(typical * 10**6),
                         worst_absolute_error_ppm=round(worst * 10**6)))
        self.assertEqual(report["selected_weight_percent"], selected)
        self.assertEqual(selected, 50)
        for public, suffix in zip(report["prior_anchor_control"], ("", "-repeat"), strict=True):
            boot, audit = extract("streams", suffix, diagnostics=True)
            self.assertEqual(public["boot_sha256"], boot)
            self.assertEqual(set(public["cases"]), {"ascending", "shuffled", "lanes32"})
            for (name, program), (middle, stream, native) in audit.items():
                adjusted = ceil(middle + Fraction(selected * (stream - middle), 100))
                self.assertEqual(public["cases"][name][program], dict(native_ns=median(native), midpoint_ns=middle,
                                                                     branch_stream_ns=stream, selected_ns=adjusted))
        for public, suffix in zip(report["evaluation"], ("", "-repeat"), strict=True):
            boot, pairs = extract("lazy", suffix)
            self.assertEqual(boot, public["boot_sha256"])
            errors_by_pair = defaultdict(set)
            for method, weight in (("midpoint", 0), ("selected", selected), ("branch_stream", 100)):
                rates, grid = [], Counter()
                for pair, (prediction, error, native) in enumerate(observations(pairs, weight)):
                    self.assertEqual(len(native), 9)
                    rates.append(error)
                    for ms in range(30, 86, 5):
                        ns = ms * 10**6
                        decision = ("false_pass" if prediction <= ns < median(native)
                                    else "false_timeout" if median(native) <= ns < prediction else "correct")
                        grid["majority_" + decision] += 1
                        errors_by_pair[method, "majority", decision].add((pair, ms))
                        if min(native) <= ns < max(native):
                            grid["mixed"] += 1
                        else:
                            grid["unanimous_" + decision] += 1
                            errors_by_pair[method, "unanimous", decision].add((pair, ms))
                metrics = dict(public["methods"][method])
                self.assertEqual(Counter(metrics.pop("grid")), grid)
                self.assertEqual(metrics, dict(cases=12, within_20pct=sum(r <= Fraction(1, 5) for r in rates),
                                 median_absolute_error_ppm=round(median(rates) * 10**6),
                                 worst_absolute_error_ppm=round(max(rates) * 10**6)))
            for rule in ("unanimous", "majority"):
                expected = {}
                for decision in ("false_timeout", "false_pass"):
                    old = errors_by_pair["branch_stream", rule, decision]
                    new = errors_by_pair["selected", rule, decision]
                    expected[decision + "_added"] = len(new - old)
                    expected[decision + "_fixed"] = len(old - new)
                self.assertEqual(public["selected_vs_branch_stream"][rule], expected)
