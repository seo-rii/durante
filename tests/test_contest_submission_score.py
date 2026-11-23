"""Check withheld submission timing, exact frozen coefficients and fixed-limit vote semantics."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments"))
from contest_submissions import score


COUNTERS = dict(instructions=1_600_000, rep_bytes=4_000, cycles_x16=16_000_000, memory_operand_bytes=50_000)
MS = 1_000_000


def case(reference=10 * MS, native=None):
    samples = [reference + 400, reference, reference - 300]
    return dict(reference_elf_sha256="1" * 64, submission_elf_sha256="2" * 64,
                reference_counter=COUNTERS.copy(), submission_counter=COUNTERS.copy(),
                reference_samples_ns=samples, native_samples_ns=[10 * MS] * 9 if native is None else native,
                fixed_limits_ms=[9, 10, 11], predictions=score.predict(COUNTERS, COUNTERS, samples))


class SubmissionScores(unittest.TestCase):
    def test_unrounded_work_ratio_and_sixteenth_cycle_units(self):
        half = {k: v // 2 for k, v in COUNTERS.items()}
        self.assertEqual(score.predict(COUNTERS, half, [1_000_002, 1_000_000, 1_000_001]), {
            "frozen_ns": 199351, "transfer_ns": 500001, "latency_frozen_ns": 194439, "latency_transfer_ns": 500001,
        })

    def test_native_submission_times_can_only_change_the_score(self):
        row = case()
        other = dict(row, native_samples_ns=[18 * MS] * 9)
        scored = score.evaluate({"rice/a": row, "rice/b": other})
        a, b = scored["cases"]["rice/a"], scored["cases"]["rice/b"]
        self.assertEqual(a["predictions"], b["predictions"])
        self.assertNotEqual(a["signed_error_ppm"], b["signed_error_ppm"])
        self.assertEqual(scored["programs"]["rice"], scored["all"])
        self.assertEqual(scored["all"]["transfer_ns"]["within_20pct"], 1)
        self.assertEqual(scored["all"]["transfer_ns"]["false_accept"], 2)

    def test_strict_equality_mixed_native_votes_and_unanimous_false_tle(self):
        row = case(native=[9 * MS] * 4 + [11 * MS] * 5)
        scored = score.evaluate({"rice/a": row})
        trial = scored["cases"]["rice/a"]
        self.assertEqual(trial["native_cpu"], dict(samples=9, min_ns=9 * MS, max_ns=11 * MS,
                                                  median_ns=11 * MS, spread_ppm=181818))
        points = trial["fixed_deadlines"]["transfer_ns"]
        self.assertEqual([(p["native_tle"], p["model_tle"]) for p in points],
                         [(5, True), (5, False), (0, False)])
        totals = scored["all"]["transfer_ns"]
        self.assertEqual([totals[k] for k in ("mixed", "majority_correct", "unanimous_correct",
                                               "false_tle", "false_accept")], [2, 2, 1, 0, 0])
        one_over = score.evaluate({"rice/b": case(reference=10 * MS + 1)})
        self.assertEqual(one_over["all"]["transfer_ns"]["false_tle"], 1)

    def test_twenty_percent_gate_uses_unrounded_error(self):
        result = score.evaluate({"rice/a": case(12 * MS), "rice/b": case(12 * MS + 1)})
        self.assertEqual(result["all"]["transfer_ns"]["within_20pct"], 1)

    def test_original_and_submission_must_be_distinct_across_all_cases(self):
        original = case()
        other = case()
        other.update(reference_elf_sha256=original["submission_elf_sha256"], submission_elf_sha256="3" * 64)
        with self.assertRaisesRegex(ValueError, "disjoint"):
            score.evaluate({"rice/a": original, "scrivener/b": other})

    def test_rejects_stale_seal_invalid_counter_and_bad_samples(self):
        mutations = [("predictions", {"transfer_ns": 1}), ("predictions", {"frozen_ns": True}),
                     ("reference_counter", {"rep_bytes": -1}), ("submission_counter", {"cycles_x16": 0}),
                     ("reference_counter", {"instructions": 2**64}), ("reference_counter", {"instructions": True})]
        for field, changes in mutations:
            with self.subTest(field=field, changes=changes), self.assertRaises(ValueError):
                row = case()
                row[field].update(changes)
                score.evaluate({"rice/a": row})
        invalid = [("reference_samples_ns", [10 * MS] * 9), ("native_samples_ns", [10 * MS] * 3),
                   ("native_samples_ns", [True] * 9), ("fixed_limits_ms", [True]), ("fixed_limits_ms", [10, 9]),
                   ("fixed_limits_ms", [10, 10])]
        for field, values in invalid:
            with self.subTest(field=field, values=values), self.assertRaises(ValueError):
                row = case()
                row[field] = values
                score.evaluate({"rice/a": row})
