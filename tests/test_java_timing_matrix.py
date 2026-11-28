import copy
import hashlib
import importlib.util
import itertools
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

source = Path(__file__).resolve().parents[1] / "experiments/java_timing/summarize.py"
spec = importlib.util.spec_from_file_location("java_timing_summary", source)
timing = importlib.util.module_from_spec(spec)
spec.loader.exec_module(timing)


def matrix():
    problems = ("first", "second")
    langs = {"java": ["current", "alternate"], "java-cds": ["current"]}
    data = {"schema": 1,
            "provenance": {"rounds": 3, "configs": {"current": {}, "alternate": {}},
                           "input_sha256": {p: hashlib.sha256(p.encode()).hexdigest() for p in problems}},
            "langs": langs, "samples": []}
    for lang, configs in langs.items():
        arms = [("native", "native"), *itertools.product(configs, ("durante", "budget-large"))]
        for problem in problems:
            for config, mode in arms:
                for index, base in enumerate((1, 4, 9)):
                    factor = {"native": 1, "durante": 2, "budget-large": 3}[mode]
                    if problem == "second" and mode != "native":
                        factor *= 4
                    row = dict(lang=lang, problem=problem, mode=mode, config=config, round=index,
                               cpu_s=base*factor, wall_s=base*factor+0.25, output_ok=True, exit=0)
                    if mode != "native":
                        row["valid"] = True
                    data["samples"].append(row)
    return data


class JavaTimingMatrix(unittest.TestCase):
    def test_existing_aggregation_and_shuffled_rows(self):
        data = matrix()
        actual = timing.summarize(data)
        self.assertEqual(actual["rounds"], 3)
        configs = actual["profiles"]["java"]["configs"]["alternate"]
        self.assertAlmostEqual(configs["durante"]["geomean_ratio_min"], 4.0)
        self.assertAlmostEqual(configs["budget-large"]["geomean_ratio_median"], 6.0)
        self.assertEqual(configs["budget-large"]["per_problem"]["second"],
                         {"min": 12, "median": 48, "ratio_min": 12.0, "ratio_median": 12.0})
        data["samples"].reverse()
        self.assertEqual(actual, timing.summarize(data))

    def test_missing_round_duplicate_round_and_entire_problem(self):
        for modification in ("drop", "replace", "problem", "equal_total_different_cell"):
            with self.subTest(modification=modification):
                data = matrix()
                rows = data["samples"]
                if modification == "drop":
                    rows.pop()
                elif modification == "replace":
                    rows[-1]["round"] = rows[-2]["round"]
                elif modification == "problem":
                    rows[:] = [r for r in rows if r["problem"] != "second"]
                else:
                    rows[-1] = copy.deepcopy(rows[0])
                with self.assertRaisesRegex(ValueError, "missing sample|duplicate sample"):
                    timing.summarize(data)

    def test_undeclared_rows_and_bad_round_ids(self):
        for field, value in (("lang", "java-other"), ("config", "alternate"), ("mode", "half"),
                             ("problem", "third"), ("round", -1), ("round", 3), ("round", False), ("round", 0.0)):
            with self.subTest(field=field, value=value):
                data = matrix()
                data["samples"][-1][field] = value  # last row belongs to java-cds/current
                with self.assertRaisesRegex(ValueError, "undeclared sample|invalid round"):
                    timing.summarize(data)

    def test_invalid_timings_and_failed_measurements(self):
        changes = [(field, v) for field in ("wall_s", "cpu_s")
                   for v in (0, -1, True, "1.2", float("nan"), float("inf"), float("-inf"))]
        changes.extend((("exit", False), ("exit", 1), ("output_ok", 1), ("valid", False)))
        for field, value in changes:
            with self.subTest(field=field, value=value):
                data = matrix()
                data["samples"][-1][field] = value
                with self.assertRaisesRegex(ValueError, "invalid CPU or wall time|failed sample"):
                    timing.summarize(data)

    def test_invalid_declarations(self):
        for key, value in (("rounds", True), ("rounds", 0), ("input_sha256", {}),
                           ("input_sha256", {"first": "bad digest"}), ("configs", {"current": {}})):
            with self.subTest(key=key):
                data = matrix()
                data["provenance"][key] = value
                with self.assertRaises(ValueError):
                    timing.summarize(data)
        data = matrix()
        data["langs"]["java"].append("current")
        with self.assertRaisesRegex(ValueError, "duplicate configuration"):
            timing.summarize(data)

    def test_collector_rejects_ambiguous_schedule_before_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "raw.json"
            base = [sys.executable, "-B", str(source.with_name("run.py")), "--output", str(out),
                    "--runner-bin", "unused-runner", "--config", "main=unused-sdk,unused-client",
                    "--lang", "java=main", "--problem", "bfs_grid"]
            for extra, message in ((["--config", "main=x,y"], "repeated configuration"),
                                   (["--lang", "java=main"], "repeated profile"),
                                   (["--lang", "java-cds=missing"], "unknown configuration"),
                                   (["--problem", "bfs_grid"], "name each problem once"),
                                   (["--rounds", "0"], "positive rounds")):
                with self.subTest(extra=extra):
                    child = subprocess.run(base + extra, capture_output=True, text=True, timeout=10)
                    self.assertEqual(child.returncode, 2)
                    self.assertIn(message, child.stderr)
                    self.assertFalse(out.exists())

    def test_nonfinite_derived_aggregate_fails(self):
        data = matrix()
        for r in data["samples"]:
            r["cpu_s"] = 1e-300 if r["mode"] == "native" else 1e300
        with self.assertRaisesRegex(ValueError, "aggregate timing"):
            timing.summarize(data)


if __name__ == "__main__":
    unittest.main()
