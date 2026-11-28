import gzip
import json
import unittest
from fractions import Fraction

from replay import Cell, HERE, checked_row, intersect, replay


class Constraints(unittest.TestCase):
    def test_exact_deadline_and_disjoint_open_endpoint(self):
        # Differential score is k - 1/3; AC at equality.
        at = Fraction(1, 3)
        accepts = Cell("a", "accept", (-1, 3), (-1, 3))
        times_out = Cell("b", "timeout", (-1, 3), (-1, 3))
        self.assertEqual(accepts.forecast(at), "accept")
        self.assertEqual((intersect([accepts]).upper, intersect([times_out]).lower), (at, at))
        self.assertFalse(intersect([accepts, times_out]).feasible)

    def test_cross_pair_bounds(self):
        cell = Cell("p", "accept", (-3, 1), (-1, 1))
        self.assertEqual([cell.forecast(k) for k in (1, 2, 3, 4)],
                         ["accept", "indeterminate", "indeterminate", "timeout"])
        self.assertEqual(intersect([cell]).upper, 1)
        self.assertFalse(intersect([Cell("x", "timeout", (0, 0), (0, 0))]).feasible)

    def test_registered_source_and_decisions(self):
        report = replay()
        golden = json.loads((HERE / "results.json").read_bytes())
        self.assertEqual(json.loads(json.dumps(report)), golden)
        self.assertEqual(report["baseline"]["milan"]["outcomes"]["primary"],
                         {"agree": 16, "indeterminate": 1, "false_accept": 1})
        self.assertEqual(report["baseline"]["aonohako_shortest_paths"]["outcomes"]["pooled"], {"agree": 6})
        self.assertTrue(all(not r["training"]["feasible"] for r in report["candidates"].values()))
        self.assertTrue(report["candidates"]["indirect"]["target_and_holdout"]["feasible"])
        self.assertFalse(report["candidates"]["direct"]["target_and_holdout"]["feasible"])

    def test_instruction_control_independent_of_event_report(self):
        local = json.loads(gzip.decompress((HERE / "local.json.gz").read_bytes()))
        row = local["panels"]["milan"]["rows"][0]
        row["events"]["instructions"] += 1
        with self.assertRaisesRegex(ValueError, "counter disagreement"):
            checked_row(row, row["stdout_sha256"])


if __name__ == "__main__":
    unittest.main()
