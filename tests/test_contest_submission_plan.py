"""The two solutions to each problem use the same registered input and oracle."""
import json
from pathlib import Path
import sys
import unittest

EXPERIMENTS = Path(__file__).resolve().parents[1] / "experiments"
sys.path.insert(0, str(EXPERIMENTS))
from calibrated_limits.prepare import registered_programs
from contest_submissions.bench import make_input
from cpp20_locality.verify import sha


class SubmissionRegistration(unittest.TestCase):
    def test_alternate_executables_and_original_oracles(self):
        cases = json.loads((EXPERIMENTS / "contest_submissions/plan.json").read_bytes())["cases"]
        previous = {}
        for study in ("contest_accuracy", "contest_warm", "contest_scale"):
            plan = json.loads((EXPERIMENTS / study / "plan.json").read_bytes())
            previous.update({row["case_id"]: row for row in plan["cases"]})
        programs = registered_programs(cases)
        self.assertEqual(set(programs), {"rice", "scrivener", "rice_window", "scrivener_lifting"})
        self.assertEqual(len(set(programs.values())), 4)
        for case in cases:
            with self.subTest(case=case["case_id"]):
                stdin, stdout = make_input(case)
                original = previous[case["case_id"]]
                self.assertEqual(programs[case["program"]], original["binary_sha256"])
                self.assertEqual((sha(stdin.encode()), sha(stdout.encode())),
                                 (case["stdin_sha256"], case["stdout_sha256"]))
                self.assertEqual((case["stdin_sha256"], case["stdout_sha256"]),
                                 (original["stdin_sha256"], original["stdout_sha256"]))

    def test_single_role_plans_and_conflicting_registration(self):
        cases = json.loads((EXPERIMENTS / "contest_scale/plan.json").read_bytes())["cases"]
        self.assertEqual(registered_programs(cases), {c["program"]: c["binary_sha256"] for c in cases})
        with self.assertRaisesRegex(ValueError, "conflicting registered ELF"):
            registered_programs([cases[0], dict(cases[0], binary_sha256="0" * 64)])
