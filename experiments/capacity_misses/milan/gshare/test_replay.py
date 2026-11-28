import copy
from fractions import Fraction
from pathlib import Path
import runpy
import unittest

HERE = Path(__file__).resolve().parent
study = runpy.run_path(str(HERE / "replay.py"))


class BranchTransferTest(unittest.TestCase):
    def test_reproduces_the_two_boot_decisions_and_archived_fit(self):
        result = study["evaluate"](study["read"](HERE / "records.json"))
        self.assertEqual(result, study["read"](HERE / "results.json"))
        row = next(d for d in result["decisions"] if d["case"] == "dijkstra--set_decrease_key" and d["factor"] == 2)
        self.assertEqual(row["native"], dict(primary="timeout", repeat="indeterminate", combined="indeterminate"))
        self.assertEqual(row["clocks"]["with_gshare"], "accept")

    def test_ceiling_handles_missing_anchor_activity_without_claiming_a_finite_bound(self):
        ceiling = study["component_ceiling"]
        self.assertIsNone(ceiling([{"a": 1, "b": 0}], [{"a": 0, "b": 2}]))
        self.assertEqual(ceiling([{"a": 0, "b": 3}], [{"a": 0, "b": 2}]), Fraction(3, 2))
        self.assertIsNone(ceiling([{"a": 0}], [{"a": 0}]))

    def test_rejects_a_missing_repeat(self):
        evidence = study["read"](HERE / "records.json")
        evidence["rows"].pop()
        with self.assertRaisesRegex(ValueError, "trial order/coverage"):
            study["evaluate"](evidence)

    def test_rejects_disconnected_counter_or_output(self):
        row = study["read"](HERE / "records.json")["rows"][0]
        spec = study["read"](HERE.parent / "frozen.json")["cases"][row["case"]]
        for field in ("instructions", "cond_branches", "stdout_sha256"):
            with self.subTest(field=field):
                changed = copy.deepcopy(row)
                if field == "stdout_sha256":
                    changed[field] = "0" * 64
                else:
                    changed["features"][field] += 1
                with self.assertRaises(ValueError):
                    study["restored"](changed, spec)


if __name__ == "__main__":
    unittest.main()
