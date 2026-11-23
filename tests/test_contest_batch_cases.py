"""Check the cohort, prior identities and normal contest executables against all registered batch inputs."""
from collections import Counter
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments"))
from contest_batch import corpus


def plan(directory):
    return json.loads((ROOT / "experiments" / directory / "plan.json").read_bytes())


class BatchCases(unittest.TestCase):
    def test_only_two_previous_inputs_and_fixed_limits(self):
        old = {row["case_id"]: (row["binary_sha256"], row["stdin_sha256"], row["stdout_sha256"])
               for directory in ("contest_accuracy", "contest_warm") for row in plan(directory)["cases"]}
        rows = plan("contest_batch")["cases"]
        known = set(old.values())
        self.assertEqual(len({(r["binary_sha256"], r["stdin_sha256"]) for r in rows}), 12)
        self.assertEqual(Counter(r["program"] for r in rows if r["cohort"] == "prospective"),
                         {"dreaming":2, "posterize":2, "scrivener":2, "money":2, "wall":2})
        for row in rows:
            with self.subTest(case=row["case_id"]):
                identity = tuple(row[k] for k in ("binary_sha256", "stdin_sha256", "stdout_sha256"))
                self.assertEqual(row["split"], "holdout")
                if row["cohort"] == "previous":
                    self.assertIn((row["program"], row["variant"]), corpus.CONTROLS)
                    self.assertEqual(identity, old[row["case_id"]])
                else:
                    self.assertNotIn(identity[:2], {item[:2] for item in known})
                self.assertEqual(row["fixed_limits_ms"], [5,12,13,25,50] if row["case_id"] == "scrivener/distant_versions"
                                 else [5,12,25,50])

    def test_materialized_oracles_match_solvers(self):
        registered = plan("contest_batch")
        corpus.expand(registered)
        with tempfile.TemporaryDirectory() as temp:
            tmp = Path(temp)
            for program in corpus.variants.CASES:
                directory = "contest_accuracy" if program in ("money", "wall") else "contest_warm"
                src = ROOT / "experiments" / directory / (program + ".cpp")
                subprocess.run(["g++", "-O2", "-std=c++20", src, "-o", tmp/program], check=True)
            for row in registered["cases"]:
                with self.subTest(case=row["case_id"]):
                    actual = subprocess.check_output([tmp/row["program"]], text=True, input=row["stdin"])
                    self.assertEqual(actual, row["stdout"])

    def test_rejects_mutated_digest(self):
        batch = plan("contest_batch")
        # Validate a single short input against its frozen expected output.
        row = next(row for row in batch["cases"] if row["program"] == "posterize")
        for key in ("stdin_sha256", "stdout_sha256"):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "differ from registered"):
                corpus.expand({"cases":[dict(row, **{key:"0"*64})]})
