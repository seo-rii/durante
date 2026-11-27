"""Regression tests for the prospectively specified fits, boundaries and trial order."""
from copy import deepcopy
from fractions import Fraction
import json
from pathlib import Path
import unittest

import analysis


def sample(latency, memory, instructions):
    counts = dict(latency_x16=latency, memory_operand_bytes=memory, instructions=instructions, work=instructions+2)
    ns = 3*latency + 5*memory
    return {"native": [ns-2, ns-1, ns, ns+1, ns+2], "counters": [counts, counts.copy()]}


class RegisteredModels(unittest.TestCase):
    def test_exact_fit_and_held_out_labels_are_excluded(self):
        manifest = json.loads((Path(__file__).parent / "case_manifest.json").read_bytes())["cases"]
        analysis.check_cases(manifest)
        corpus = {case["case_id"]: sample(40+2*i, 3+(i%4), 2+i) for i,case in enumerate(manifest)}
        original = analysis.score(corpus, manifest)
        self.assertEqual(original["latency_memory"]["nanoseconds_per_unit"],
                         {"latency_x16": {"numerator":3,"denominator":1}, "memory_operand_bytes":{"numerator":5,"denominator":1}})
        training = [corpus[c["case_id"]] for c in manifest if c["split"] == "calibration"]
        work = [s["counters"][0]["work"] for s in training]
        expected_slope = Fraction(sum(w*s["native"][2] for w,s in zip(work,training)), sum(w*w for w in work))
        self.assertEqual(original["absolute_work"]["nanoseconds_per_unit"]["work"], analysis.ratio(expected_slope))
        for c in manifest:
            if c["split"] == "held_out":
                s = corpus[c["case_id"]]
                s["native"] = [n*13 for n in s["native"]]
        altered = analysis.score(corpus, manifest)
        for name in original:
            self.assertEqual(original[name]["nanoseconds_per_unit"], altered[name]["nanoseconds_per_unit"])
            self.assertEqual(original[name]["cases"], 12)
        self.assertEqual(original["latency_memory"]["cases_both_within_20pct"], 12)
        self.assertEqual(altered["latency_memory"]["cases_both_within_20pct"], 0)

    def test_both_counters_and_strict_boundaries(self):
        s = dict(native=[75, 95, 100, 110, 125], counters=[{"work":80}, {"work":120}])
        result = analysis.evaluate({"work":Fraction(1)}, {"case":s})
        self.assertEqual(result["cases_both_within_20pct"], 1)
        self.assertEqual(result["hypothetical_confusion"]["low"], {"true_pass":0, "false_pass":1, "true_tle":1, "false_tle":0})
        self.assertEqual(result["hypothetical_confusion"]["high"]["true_pass"], 2)
        s["counters"][1]["work"] += 1
        result = analysis.evaluate({"work":Fraction(1)}, {"case":s})
        self.assertEqual(result["cases_both_within_20pct"], 0)
        self.assertEqual(result["counter_runs_within_20pct"], 1)
        self.assertEqual(result["hypothetical_confusion"]["high"]["false_tle"], 1)

    def test_duplicate_identity_misplaced_target_and_reordered_replicate(self):
        manifest = json.loads((Path(__file__).parent / "case_manifest.json").read_bytes())["cases"]
        schedule = list(analysis.schedule(manifest))
        self.assertEqual(len({(c["case_id"],k,i) for c,k,i in schedule}), 105)
        corrupt = deepcopy(manifest)
        corrupt[-1] = corrupt[-2]
        with self.assertRaisesRegex(ValueError, "case ID"):
            analysis.check_cases(corrupt)
        wrong = dict(sequence=0, kind="counters", rep=1)
        with self.assertRaisesRegex(ValueError, "order or identity"):
            analysis.load_trials([wrong], [[]], manifest, None)
        window = [dict(cpu=2,path=analysis.DRRUN,pid=14)]
        with self.assertRaisesRegex(ValueError, "CPU"):
            analysis.placement(window, dict(kind="counters",execs=window))


if __name__ == "__main__":
    unittest.main()
