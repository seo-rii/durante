"""Reconcile the published verdicts with the archived inputs and class totals."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
STUDY = HERE.parent / "capacity_misses"
RAW = STUDY / "results/v2/results.json"


def read(path):
    return json.loads(path.read_bytes())


class VerdictReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = read(RAW)
        with TemporaryDirectory() as temp:
            output = Path(temp) / "report.json"
            subprocess.run([sys.executable, "-B", STUDY / "analyze_v2.py", RAW, "--output", output],
                           check=True, stdout=subprocess.PIPE, text=True)
            cls.score = read(output)

    def test_every_held_out_source_input_and_expected_answer_is_identified(self):
        raw = self.raw
        manifest = read(HERE / "variants/manifest.json")
        held = {name for name, case in raw["cases"].items() if case["role"] == "heldout"}
        seen = set()
        for problem, group in manifest["problems"].items():
            for variant in group["variants"]:
                source = ROOT / variant["path"]
                name = problem + "--" + source.stem
                self.assertNotIn(name, seen)
                seen.add(name)
                self.assertEqual(raw["built"][name]["source_sha256"], hashlib.sha256(source.read_bytes()).hexdigest())
                for key in ("stdin_sha256", "expected_sha256"):
                    self.assertEqual(raw["cases"][name][key], raw["cases"]["contest-" + problem][key])
        self.assertEqual(seen, held)
        self.assertEqual(len(seen), 49)

    def test_all_models_and_classes_reconcile_with_the_archived_summary(self):
        archived = read(STUDY / "results/v2/summary.json")
        document = (ROOT / "docs/JUDGE_DISAGREEMENT.md").read_text()
        self.assertEqual(set(self.score["models"]), set(archived["models"]))
        for model, result in self.score["models"].items():
            self.assertEqual(result["verdicts"], archived["models"][model]["verdicts"])
            table_row, = (r for r in document.splitlines() if r.startswith("| " + model + " "))
            cells = [value.strip() for value in table_row.strip("|").split("|")[1:]]
            settings = [f"{policy}@{k}" for policy in ("R", "A") for k in (1.5, 2.0, 3.0)]
            expected = []
            for setting in settings:
                v = result["verdicts"][setting]
                expected.append(f"{v['disagree']}/{v['decisions']} ({v['false_tle']}/{v['false_ac']})")
            self.assertEqual(cells, expected, model)
            self.assertEqual(result["verdicts"].keys(), result["verdicts_by_class"].keys())
            for setting, total in result["verdicts"].items():
                classes = result["verdicts_by_class"][setting].values()
                self.assertEqual(total["decisions"] + total["ambiguous"], 49)
                for field in ("decisions", "ambiguous", "disagree", "false_ac", "false_tle"):
                    self.assertEqual(sum(c[field] for c in classes), total[field], (model, setting, field))
        pointer = self.score["models"]["M0"]["verdicts_by_class"]["R@2.0"]["pointer chasing"]
        self.assertEqual((pointer["decisions"], pointer["ambiguous"], pointer["false_ac"]), (4, 2, 2))

    def test_too_slow_exceptions_and_relative_factor_trends(self):
        exceptions = {(model, d["case"], d["k"], d["policy"]) for model, result in self.score["models"].items()
                      for d in result["decisions"] if d["too_slow"] and not d["ambiguous"] and d["model_ac"]}
        expected = {("ML", k) for k in (1.5, 2.0, 3.0)} | {(model, 3.0) for model in ("M0", "M1", "ML4")}
        self.assertEqual(exceptions, {(model, "deep_dfs--map_of_sets", k, "A") for model, k in expected})
        for model, expected in (("M0", [7, 6, 5]), ("M2", [3, 4, 5])):
            verdicts = self.score["models"][model]["verdicts"]
            self.assertEqual([verdicts[f"R@{k}"]["disagree"] for k in (1.5, 2.0, 3.0)], expected)


if __name__ == "__main__":
    unittest.main()
