import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments"))
from deadline_accuracy.verify import annotate, summarize, thresholds, verify
from shape_calibration.verify_combined import unpack

RESULT = ROOT / "experiments/results/gce-deadline-accuracy"
EXACT = ROOT / "experiments/results/gce-deadline-exact-one"


class DeadlineScoring(unittest.TestCase):
    def test_ceiling_minimum_and_fixed_overlap(self):
        self.assertEqual(thresholds(1, [60, 85, 100, 115, 150], [1, 3]),
                         [(1, True, [60, 85, 100, 115, 150]), (3, True, [])])
        self.assertEqual(thresholds(100_000_001, [100], []), [(101, False, [100])])

    def test_separates_mixed_majority_from_unanimous_errors(self):
        def result(ns, tle):
            return dict(preflight_ns=ns, prediction_ns=ns, observation="terminal", tle=tle)
        trial = dict(baseline_ns=9_000_000, packet_median_ns=11_000_000, deadlines=[
            dict(limit_ms=10, near=True, fixed=True, adaptive_percent=[100],
                 baseline=result(9_000_000, False), gate=result(11_000_000, True)),
            dict(limit_ms=8, near=True, fixed=False, adaptive_percent=[85],
                 baseline=result(8_000_000, False), gate=result(12_000_000, True))])
        annotate(trial, [9_000_000] * 4 + [12_000_000] * 5)
        scores = summarize(trial["deadlines"])
        self.assertEqual(trial["deadlines"][0]["native_median_margin_ns"], 2_000_000)
        self.assertEqual((scores["baseline"]["mixed_native"], scores["baseline"]["false_accept"]), (1, 1))
        self.assertEqual(scores["baseline"]["majority_matches"], 0)
        self.assertEqual(scores["gate"]["majority_matches"], 2)
        self.assertEqual(scores["fixed_and_adaptive"], 1)


class ArchivedReplay:
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.output = unpack(cls.results / "output.tar.xz", Path(cls.temp.name))

    def mutate(self, path, change, error):
        raw = path.read_bytes()
        try:
            value = json.loads(raw)
            change(value)
            path.write_text(json.dumps(value))
            with self.assertRaisesRegex(ValueError, error):
                verify(self.output, self.strategy)
        finally:
            path.write_bytes(raw)


class AccuracyReplay(ArchivedReplay, unittest.TestCase):
    results = RESULT
    strategy = "near-three"

    def test_matches_published_accuracy_metrics(self):
        result = verify(self.output)
        self.assertEqual(result, json.loads((RESULT / "metrics.json").read_text()))
        self.assertEqual(result["totals"]["all"]["inputs"], 15)
        self.assertEqual(result["totals"]["prospective"]["inputs"], 14)
        self.assertEqual(result["native_runs"], 135)
        for group in ("all", "prospective", "previous"):
            tally = result["totals"][group]["deadlines"]["all"]
            for arm in ("baseline", "gate"):
                self.assertEqual(tally["limits"], sum(tally[arm][k] for k in
                                 ("agreement", "false_accept", "false_tle", "mixed_native")))

    def test_rejects_changed_first_limit(self):
        def change(summary):
            first = next(row for row in summary["rows"] if row["limits"])
            first["limits"][0]["limit_ms"] += 1
        self.mutate(self.output / "summary.json", change, "changed deadline")

    def test_rejects_different_median_policy(self):
        packet = next((self.output / "cases").rglob("deadline-packet/packet.json"))
        def change(receipt):
            receipt["median_index"] = (receipt["median_index"] + 1) % 3
        self.mutate(packet, change, "invalid median packet")

    def test_holds_out_native_reference_processes(self):
        def change(summary):
            summary["native"][0]["pid"] = summary["rows"][0]["profile_pid"]
        self.mutate(self.output / "summary.json", change, "duplicate physical workload PID")

    def test_rejects_unbound_live_result(self):
        path = next((self.output / "cases").rglob("limits/gate/00/result.json"))
        self.mutate(path, lambda value:value.update(selection_sha256="0"*64), "wrapped decision differs")

class ExactInputConfirmation(ArchivedReplay, unittest.TestCase):
    results = EXACT
    strategy = "exact-one"

    def test_reproduces_actual_exact_and_baseline_runs(self):
        result = verify(self.output, self.strategy)
        self.assertEqual(result, json.loads((EXACT / "metrics.json").read_text()))
        self.assertEqual(result["native_runs"], 12 * 9)
        primary = json.loads((ROOT / "experiments/deadline_accuracy/plan.json").read_text())
        exact = json.loads((ROOT / "experiments/deadline_accuracy/exact-plan.json").read_text())
        known = {(case["binary_sha256"], case["stdin"]) for case in primary["cases"]}
        new_pairs = {(case["binary_sha256"], case["stdin"]) for case in exact["cases"] if case["cohort"] == "prospective"}
        self.assertEqual(len(new_pairs), 10)
        self.assertFalse(new_pairs & known)
        new = [c for c in result["cases"].values() if c["cohort"] == "prospective"]
        self.assertEqual(len(new), 10)
        self.assertEqual(len({c["program"] for c in new}), 5)
        self.assertEqual(result["totals"]["previous"]["inputs"], 2)
        for case in result["cases"].values():
            self.assertEqual(len(case["extra_ns"]), int(case["baseline_paid_cpu_ns"] == 0))
            self.assertEqual(case["packet_exact_ns"], case["baseline_paid_cpu_ns"] or case["extra_ns"][0])
            for p in case["deadlines"]:
                self.assertEqual(p["exact"]["preflight_ns"], case["packet_exact_ns"])

    def test_exact_fixed_random_branch_and_mixed_fenwick_boundary(self):
        cases = verify(self.output, self.strategy)["cases"]
        def deadline(case, ms):
            return next(p for p in cases[case]["deadlines"] if p["limit_ms"] == ms)

        branch = deadline("threshold_runs/random_larger_stream", 75)
        self.assertTrue(branch["fixed"])
        self.assertFalse(branch["near"])
        self.assertEqual(branch["native_tle_reps"], 9)
        self.assertEqual((branch["baseline"]["tle"], branch["exact"]["tle"]), (False, True))
        self.assertEqual((branch["baseline"]["native_class"], branch["exact"]["native_class"]),
                         ("false_accept", "agreement"))

        fenwick = deadline("fenwick/seven_half_mib", 184)
        self.assertFalse(fenwick["fixed"])
        self.assertEqual(fenwick["native_tle_reps"], 5)
        self.assertEqual((fenwick["baseline"]["tle"], fenwick["exact"]["tle"]), (True, False))
        self.assertEqual((fenwick["baseline"]["native_class"], fenwick["exact"]["native_class"]),
                         ("mixed_native", "mixed_native"))
        self.assertEqual((fenwick["baseline"]["majority_match"], fenwick["exact"]["majority_match"]), (True, False))

    def test_requires_exact_payment_even_far_from_baseline(self):
        def change(summary):
            event = next(e for row in summary["rows"] for e in row["limits"] if e["method"] == "exact" and not e["near"])
            event["selection_path"] = "baseline-selection.json"
        self.mutate(self.output / "summary.json", change, "recorded enforcement belongs to a different policy")

    def test_rejects_first_payment_recorded_after_enforcement(self):
        case = self.output / "cases/threshold_runs/random"
        policy = json.loads((case / "deadline-packet/policy-0.json").read_text())
        live = json.loads((case / "limits/exact/00/result.json").read_text())
        trace = self.output / "exec-trace"
        original = trace.read_bytes()
        lines = original.splitlines(keepends=True)
        def positions(pid):
            return [i for i, line in enumerate(lines) if f" pid={pid} ".encode() in line]

        (native,) = positions(policy["native"]["pid"])
        first, last = positions(live["pid"])
        self.assertLess(native, first)
        payment = lines.pop(native)
        lines.insert(last, payment)
        try:
            trace.write_bytes(b"".join(lines))
            with self.assertRaisesRegex(ValueError, "exec trace order differs"):
                verify(self.output, self.strategy)
        finally:
            trace.write_bytes(original)

    def test_forbids_a_second_sample_even_when_not_selected(self):
        policy = next((self.output / "cases").rglob("deadline-packet/policy-0.json"))
        extra = policy.with_name("policy-1.json")
        with extra.open("xb") as out:
            out.write(policy.read_bytes())
        try:
            with self.assertRaisesRegex(ValueError, "unexpected packet sample"):
                verify(self.output, self.strategy)
        finally:
            extra.unlink()

    def test_rejects_changing_the_observation_rule(self):
        path = next((self.output / "cases").rglob("deadline-packet/packet.json"))
        self.mutate(path, lambda receipt: receipt.update(strategy="near-three"), "invalid first-exact packet")
