import copy
from fractions import Fraction
import json
from pathlib import Path
from statistics import median
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments"))
from deadline_accuracy import client_parity
from shape_calibration.verify_combined import unpack

RESULT = ROOT / "experiments/results/gce-deadline-client-parity"


class ClientParityReplay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temp.cleanup)
        cls.output = unpack(RESULT / "output.tar.xz", Path(temp.name))
        cls.verified = client_parity.verify(cls.output)
        cls.study = json.loads((client_parity.HERE / "client-parity.json").read_bytes())
        cls.case = "successor_walk/small_blocks"

    def test_replays_published_counters_within_registered_bounds(self):
        self.assertEqual(self.verified, json.loads((RESULT / "metrics.json").read_bytes()))
        self.assertEqual(self.verified["verified_pids"], 120)
        self.assertEqual(len(self.verified["cases"]), 20)
        self.assertTrue(self.verified["cases"][self.case]["median_exact"])
        self.assertFalse(self.verified["cases"][self.case]["all_instruction_counts_equal"])

    def test_rejects_changed_rep_breakdown_in_one_repeat(self):
        counts = copy.deepcopy(self.verified["cases"][self.case]["counts"])
        sample = counts["candidate"][0]
        self.assertGreater(sample[2], 0)
        sample[1] += 1
        sample[2] -= 1
        with self.assertRaisesRegex(ValueError, "MOVS/STOS bytes differ"):
            client_parity.compare_counts(self.case, counts, self.study)

    def test_signed_median_changes_at_registered_ppm_boundary(self):
        counts = copy.deepcopy(self.verified["cases"][self.case]["counts"])
        ref = median(row[0] for row in counts["released"])
        allowed = int(Fraction(self.study["median_tolerance_ppm"] * ref, 1_000_000))
        self.assertGreater(allowed, self.study["absolute_instruction_tolerance"])
        for sign in (-1, 1):
            with self.subTest(sign=sign):
                for row in counts["candidate"]:
                    row[0] = ref + sign * allowed
                self.assertTrue(client_parity.compare_counts(self.case, counts, self.study)["within_tolerance"])
                for row in counts["candidate"][:2]:
                    row[0] += sign
                with self.assertRaisesRegex(ValueError, "instruction medians differ"):
                    client_parity.compare_counts(self.case, counts, self.study)

    def test_absolute_instruction_floor_for_short_work(self):
        counts = {"released": [[1000, 0, 0]] * 3, "candidate": [[1032, 0, 0]] * 3}
        self.assertTrue(client_parity.compare_counts("short", counts, self.study)["within_tolerance"])
        counts["candidate"] = [[1033, 0, 0]] * 3
        with self.assertRaisesRegex(ValueError, "instruction medians differ"):
            client_parity.compare_counts("short", counts, self.study)

    def test_rejects_live_page_that_differs_from_terminal_record(self):
        path = self.output / "runs/000/clock.bin"
        original = path.read_bytes()
        try:
            path.write_bytes(bytes(len(original)))
            with self.assertRaisesRegex(ValueError, "terminal live clock mismatch"):
                client_parity.verify(self.output)
        finally:
            path.write_bytes(original)


if __name__ == "__main__":
    unittest.main()
