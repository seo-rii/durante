"""Check the scale-study input registration, independent answers and normal text graders."""
from collections import Counter
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments"))
from contest_scale import corpus, variants


def plan(directory):
    return json.loads((ROOT / "experiments" / directory / "plan.json").read_bytes())


class ScaleCases(unittest.TestCase):
    def test_small_wall_inputs_against_direct_simulation(self):
        for name in variants.CASES["wall"]:
            for n in (1, 19, 94):
                for k in (1, 17, 251):
                    with self.subTest(name=name, n=n, k=k):
                        text, expected = variants.wall(name, n, k)
                        header, *lines = text.splitlines()
                        self.assertEqual((header, len(lines)), (f"{n} {k}", k))
                        wall = [0] * n
                        for line in lines:
                            op, left, right, h = map(int, line.split())
                            self.assertTrue(0 <= left <= right < n and 0 <= h <= 100_000 and op in (1, 2))
                            for pos in range(left, right + 1):
                                wall[pos] = (max if op == 1 else min)(wall[pos], h)
                        self.assertEqual(expected, "".join(f"{h}\n" for h in wall))

    def test_small_editor_replays_complete_versions(self):
        for name in variants.CASES["scrivener"]:
            with self.subTest(name=name):
                text, expected = variants.editor(name, pages=83, visits=810)
                header, *lines = text.splitlines()
                self.assertEqual(len(lines), int(header))
                history, answers = [""], []
                for line in lines:
                    op, value = line.split()
                    if op == "T":
                        self.assertRegex(value, "^[a-z]$")
                        history.append(history[-1] + value)
                    elif op == "U":
                        self.assertTrue(1 <= int(value) < len(history))
                        history.append(history[-int(value)-1])
                    else:
                        self.assertEqual(op, "P")
                        self.assertTrue(0 <= int(value) < len(history[-1]))
                        answers.append(history[-1][int(value)])
                self.assertEqual(expected, "".join(ch + "\n" for ch in answers))

    def test_small_markets_against_all_pairs(self):
        for name in variants.CASES["money"]:
            for size in (8, 48, 432):
                with self.subTest(name=name, size=size):
                    text, expected = variants.market(name, size)
                    header, *lines = text.splitlines()
                    self.assertEqual((header, len(lines)), (f"{size} {size}", 2 * size))
                    offers = [tuple(map(int, line.split())) for line in lines]
                    self.assertTrue(all(1 <= value <= 10**9 for offer in offers for value in offer))
                    best = max(max(0, q-p)*max(0, e-d) for p,d in offers[:size] for q,e in offers[size:])
                    self.assertEqual(expected, f"{best}\n")

    def test_registered_cases_and_real_solvers(self):
        registered = plan("contest_scale")
        self.assertEqual(registered["native_rounds"], 11)
        self.assertEqual(registered["deadline_percent"], [85,100,115])
        cases = registered["cases"]
        self.assertEqual(len(cases), 8)
        self.assertEqual(len({row["stdin_sha256"] for row in cases}), len(cases))
        self.assertEqual(Counter(r["program"] for r in cases if r["cohort"] == "prospective"),
                         {"scrivener": 2, "wall": 2, "money": 2})
        history = {r["case_id"]: r for study in ("contest_accuracy", "contest_warm", "contest_batch")
                   for r in plan(study)["cases"]}
        known_inputs = {row["stdin_sha256"] for row in history.values()}
        sources = {"wall": "contest_accuracy", "money": "contest_accuracy", "scrivener": "contest_warm"}
        corpus.expand(registered)
        with tempfile.TemporaryDirectory() as tmp:
            for name, directory in sources.items():
                src = ROOT / "experiments" / directory / (name + ".cpp")
                subprocess.run(["g++", "-std=c++20", "-O2", src, "-o", Path(tmp)/name], check=True)
            for row in cases:
                with self.subTest(case=row["case_id"]):
                    self.assertEqual(row["split"], "holdout")
                    if (row["program"], row["variant"]) in corpus.CONTROLS:
                        self.assertEqual(row["cohort"], "previous")
                        old = history[row["case_id"]]
                        for key in ("binary_sha256", "stdin_sha256", "stdout_sha256"):
                            self.assertEqual(old[key], row[key])
                        self.assertEqual(row["fixed_limits_ms"], [12,13,14])
                    else:
                        self.assertEqual(row["cohort"], "prospective")
                        self.assertNotIn(row["stdin_sha256"], known_inputs)
                        self.assertEqual(row["fixed_limits_ms"], [50,100,200,400])
                    stdin, stdout = row["stdin"].encode(), row["stdout"].encode()
                    self.assertLessEqual(len(stdout), 1 << 20)
                    self.assertEqual(subprocess.check_output([Path(tmp)/row["program"]], input=stdin), stdout)

    def test_digest_mismatch_fails_closed(self):
        row = next(r for r in plan("contest_scale")["cases"] if r["case_id"] == "wall/crossed_intervals")
        for key in ("stdin_sha256", "stdout_sha256"):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "differ from registered"):
                corpus.expand({"cases": [dict(row, **{key: "0"*64})]})
