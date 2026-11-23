"""Replay both contest scale archives and independently score their paid clocks and split deadlines."""
from collections import defaultdict
from fractions import Fraction
from functools import cache
import json
from pathlib import Path
from statistics import median
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments"))
from contest_batch import verify as replay
from contest_scale import boundaries
from contest_warm import analysis
from shape_calibration.verify_combined import unpack

MS = 1_000_000
STUDIES = ROOT / "experiments/results"


def read(path):
    return json.loads(path.read_bytes())


def wait4_ns(row):
    return 1000 * (row["user_us"] + row["sys_us"])


class ContestScaleReplay(unittest.TestCase):
    def check_input(self, root, case, row, samples, trial):
        name = case["case_id"]
        packet = root / "batch" / name
        paid = [read(packet / f"policy-{i}.json") for i in range(4)]
        timings = [wait4_ns(policy["native"]) for policy in paid]
        first, *later = timings
        center = median(samples)
        self.assertEqual(len(samples), 11)
        self.assertEqual((trial["baseline_ns"], trial["extra_ns"], trial["packet_warm_ns"], trial["native_ns"]),
                         (first, later, median(later), samples))
        expected_id = case["binary_sha256"], case["stdin_sha256"], case["stdout_sha256"]
        for policy in paid:
            measured_id = policy["anchor"]["elf_sha256"], policy["anchor"]["stdin_sha256"], policy["native"]["stdout_sha256"]
            self.assertEqual(measured_id, expected_id)
        fixed = set(case["fixed_limits_ms"])
        adaptive = {max(1, (first * pct + 100 * MS - 1) // (100 * MS)) for pct in (85, 100, 115)}
        points = {}
        self.assertEqual([p["limit_ms"] for p in trial["deadlines"]], sorted(adaptive | fixed))
        for event in row["limits"]:
            ms = event["limit_ms"]
            idx, arm = event["index"], event["method"]
            cutoff = ms * MS
            # The flag must match the three individual pass/TLE votes, including ties at the limit.
            flags = [t > cutoff for t in later]
            window = dict(min_ns=min(later), max_ns=max(later), split=any(flags) and not all(flags))
            measured = trial["deadlines"][idx]
            if arm == "warm":
                self.assertEqual(event["calibration_window"], window)
                self.assertEqual(measured["calibration_window"], window)
            else:
                self.assertNotIn("calibration_window", event)
            receipt = read(root / "cases" / name / "limits" / arm / f"{idx:02d}" / "live/result.json")
            self.assertIn(receipt["status"], ("within_budget", "budget_exceeded"))
            tle = receipt["status"] == "budget_exceeded"
            self.assertEqual((receipt["pid"], tle), (measured[arm]["pid"], measured[arm]["tle"]))
            votes = sum(t > cutoff for t in samples)
            self.assertEqual(measured["native_tle_reps"], votes)
            point = points.setdefault(ms, dict(fixed=ms in fixed, flag=window["split"], votes=votes))
            point[arm] = tle
        errors = {arm: Fraction(ns - center, center) for arm, ns in (("baseline", first), ("warm", median(later)))}
        return dict(name=name, identity=expected_id, cohort=case["cohort"], errors=errors, points=points,
                    native_ns=samples, first_ns=first, extra_ns=later,
                    boot=paid[0]["native"]["boot_sha256"])

    def check_scores(self, data, scored, diagnostic):
        for arm in ("baseline", "warm"):
            errors = [c["errors"][arm] for c in data]
            actual = scored["actual_duration"][arm]
            self.assertEqual(actual["median_absolute_error_ppm"], round(median(map(abs, errors)) * MS))
            self.assertEqual(actual["maximum_absolute_error_ppm"], round(max(map(abs, errors)) * MS))
            self.assertEqual(actual["median_signed_error_ppm"], round(median(errors) * MS))
        changes = [abs(c["errors"]["baseline"]) - abs(c["errors"]["warm"]) for c in data]
        self.assertEqual(scored["actual_duration"]["warm"]["compared_with_baseline"],
                         dict(wins=sum(x > 0 for x in changes), ties=changes.count(0), losses=sum(x < 0 for x in changes)))
        self.assertEqual(scored["extra_native_cpu_ns"], sum(sum(c["extra_ns"]) for c in data))
        all_points = [p for c in data for p in c["points"].values()]
        for group in ("all", "fixed"):
            points = [p for p in all_points if group == "all" or p["fixed"]]
            tally = {"limits": len(points)}
            for flagged, label in ((True, "flagged"), (False, "unflagged")):
                group_points = [p for p in points if p["flag"] == flagged]
                mixed = sum(0 < p["votes"] < 11 for p in group_points)
                tally[label + "_mixed"] = mixed
                tally[label + "_unanimous"] = len(group_points) - mixed
                tally["majority_misses_" + label] = sum(p["warm"] != (p["votes"] >= 6) for p in group_points)
            self.assertEqual(diagnostic[group], tally)
            for arm in ("baseline", "warm"):
                unanimous = [p for p in points if p["votes"] in (0, 11)]
                self.assertEqual(scored[group]["actual"][arm], dict(
                    majority_correct=sum(p[arm] == (p["votes"] >= 6) for p in points),
                    unanimous_correct=sum(p[arm] == (p["votes"] == 11) for p in unanimous),
                    false_tle=sum(p[arm] and p["votes"] == 0 for p in unanimous),
                    false_accept=sum(not p[arm] and p["votes"] == 11 for p in unanimous)))

    def check_archive(self, root, archive, plan):
        # Both public report commands use the same verified, immutable archive. Replay its receipts once.
        verified = cache(replay.verify)
        with patch.object(replay, "verify", verified), patch.object(boundaries, "verify", verified):
            metrics = verified(root)
            scored = analysis.analyze(root, "interleaved-three")
            diagnostic = boundaries.report(root)
        for filename, report in (("metrics", metrics), ("analysis", scored), ("boundaries", diagnostic)):
            self.assertEqual(report, read(archive / (filename + ".json")))
        summary_path = root / "summary.json"
        summary = read(summary_path)
        native = defaultdict(list)
        for ref in summary["native"]:
            native[ref["case_id"]].append(wait4_ns(ref))
        data = [self.check_input(root, case, row, native[case["case_id"]], metrics["cases"][case["case_id"]])
                for case, row in zip(plan["cases"], summary["rows"], strict=True)]
        for cohort, count in (("prospective", 6), ("previous", 2)):
            series = [c for c in data if c["cohort"] == cohort]
            self.assertEqual(len(series), count)
            self.check_scores(series, scored["cohorts"][cohort], diagnostic["cohorts"][cohort])
        # This directory is a disposable extraction. Real verification must recompute a changed flag.
        first_warm = next(e for e in summary["rows"][0]["limits"] if e["method"] == "warm")
        first_warm["calibration_window"]["split"] = not first_warm["calibration_window"]["split"]
        summary_path.write_text(json.dumps(summary))
        with self.assertRaisesRegex(ValueError, "different three-sample calibration window"):
            replay.verify(root)
        return data

    def test_two_registered_boots_replay_scores_and_reject_changed_calibration_flags(self):
        plan = read(ROOT / "experiments/contest_scale/plan.json")
        boots, fixed_identities = [], []
        # Input/output oracles are identical on both boots. Materialize their frozen bytes once.
        with patch.object(replay, "registration_for", cache(replay.registration_for)):
            for archive_name in ("gce-contest-scale", "gce-contest-scale-repeat"):
                archive = STUDIES / archive_name
                with self.subTest(guest=archive_name), tempfile.TemporaryDirectory() as tmp:
                    root = unpack(archive / "output.tar.xz", Path(tmp))
                    data = self.check_archive(root, archive, plan)
                    boot_ids = {c["boot"] for c in data}
                    self.assertEqual(len(boot_ids), 1)
                    boots.append(boot_ids.pop())
                    fixed_identities.append({(c["name"], c["identity"], ms) for c in data
                                             for ms, point in c["points"].items() if point["fixed"]})
        self.assertEqual(len(boots), 2)
        self.assertNotEqual(*boots)
        self.assertEqual(len(fixed_identities[0]), 30)
        self.assertEqual(fixed_identities[0], fixed_identities[1])
