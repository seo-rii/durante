from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments"))
from deadline_accuracy import analysis, gate


class NativeSampleAblation(unittest.TestCase):
    def trial(self, base, references, limits, paid=0, middle=None):
        trial = dict(baseline_ns=base, baseline_paid_cpu_ns=paid, native_ns=references, cohort="prospective",
                     deadlines=[dict(limit_ms=m, near=gate.near(base, m),
                                     native_tle_reps=sum(t > m * analysis.MS for t in references)) for m in limits])
        if middle is not None:
            trial["packet_median_ns"] = middle
        return trial

    def test_first_vs_median_and_far_strict_equality(self):
        ns = analysis.MS
        trial = self.trial(110*ns, [100*ns]*9, [60, 100, 150], middle=100*ns)
        score = analysis.score_case(trial, [120*ns, 100*ns, 90*ns], False)
        total = analysis.summarize([score])
        self.assertEqual(score["source"], "transferred")
        self.assertEqual(score["reference_range_ns"], [100*ns]*2)
        self.assertEqual(total["near_decisions"]["first"]["false_tle"], 1)
        self.assertEqual(total["near_decisions"]["median"]["agreement"], 1)
        self.assertEqual(total["near_decisions"]["exact_native_ties"], 9)
        self.assertEqual(total["near_decisions"]["changed_first_to_median"], 1)
        for kind in analysis.MODELS:
            self.assertEqual(total["far_decisions"][kind]["agreement"], 2)
        self.assertEqual(total["duration"]["first"]["within_20pct"], 1)

    def test_mixed_vote_preserved_when_prediction_changes(self):
        ns = analysis.MS
        refs = [100*ns]*4 + [101*ns]*5
        trial = self.trial(100*ns, refs, [100], paid=100*ns, middle=102*ns)
        row = analysis.score_case(trial, [100*ns, 102*ns, 105*ns], True)
        scores = analysis.summarize([row])["near_decisions"]
        self.assertEqual(row["source"], "current")
        for kind in analysis.MODELS:
            self.assertEqual(scores[kind]["mixed_native"], 1)
            self.assertEqual(scores[kind]["agreement"], 0)
        self.assertEqual(scores["baseline"]["mixed_majority_disagreements"], 1)
        self.assertEqual(scores["median"]["mixed_majority_disagreements"], 0)

    def test_packet_not_invented_when_no_near_limit_was_measured(self):
        trial = self.trial(100*analysis.MS, [98*analysis.MS]*9, [10, 200])
        counts = analysis.summarize([analysis.score_case(trial, [], None)])
        self.assertEqual(counts["duration"]["median"]["cases"], 0)
        self.assertEqual(counts["far_decisions"]["median"]["agreement"], 2)
        self.assertEqual(counts["near_decisions"]["limits"], 0)
