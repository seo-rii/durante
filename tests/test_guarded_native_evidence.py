from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments"))
from guarded_native.evidence import evidence, match_case, observation


class GuardedClockEvidence(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.join = evidence()

    def test_historical_references_require_the_same_binary_and_input_output(self):
        case = self.join["cases"]["integer/1"]
        for key in ("binary_sha256", "stdin_sha256", "stdout_sha256"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                match_case(case, {**case, key: "wrong"})
        refs = [c for c in self.join["cases"].values() if c["archived_native_ns"]]
        self.assertEqual(len(refs), 13)
        self.assertTrue(all(len(c["archived_native_ns"]) == 7 and len(c["archived_metered"]) == 2 for c in refs))

    def test_different_process_clock_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "different launches"):
            observation({"launcher_pid": 123}, {"pid": 123}, {"root_pid": 124})

    def test_audit_fallback_and_matched_full_are_distinguished(self):
        cases = self.join["cases"]
        self.assertEqual(sum(len(c["rows"]) for c in cases.values()), 98)
        for name in ("path_switch/switch", "path_mix/shift"):
            rows = cases[name]["rows"]
            self.assertEqual({r["delivery_source"] for r in rows if r["kind"] == "scored"}, {"matched_full_substitute"})
            audit, = [r for r in rows if r["kind"] == "audit"]
            self.assertEqual(audit["delivery_source"], "actual_full_recovery")
            self.assertEqual(audit["delivered"]["arm"], "audit_fallback")
            self.assertNotEqual(audit["delivered"]["pid"], audit["guarded"]["pid"])
            self.assertGreater(audit["delivered"]["loads"], audit["guarded"]["loads"])
        stable = cases["path_switch/same"]["rows"][-1]
        self.assertEqual(stable["kind"], "audit")
        self.assertIsNone(stable["full"])
        self.assertEqual(stable["delivered"]["arm"], "audit_guarded")
