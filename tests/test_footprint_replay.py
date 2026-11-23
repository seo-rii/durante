import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments"))
from cpp20_locality.verify import sha
from footprint_admission.verify import METHODS, MS, score_case, summarize, verify
from shape_calibration.verify_combined import unpack

RESULT = ROOT / "experiments/results/gce-footprint-admission"


class FootprintScores(unittest.TestCase):
    def test_mixed_references_and_strict_live_boundaries(self):
        case = {"cohort": "prospective", "shape_label": "example", "limits_ms": [98, 100, 102]}
        samples = [n * MS for n in [99, 100, 100, 100, 100, 100, 100, 100, 101]]
        trial = {"profile_cpu_ns": 123}
        for method in METHODS:
            predictions = [99, 100, 102] if method == "footprint" else [98, 101, 103]
            events = [dict(limit_ms=ms, prediction_ns=value * MS, tle=value > ms,
                           observation="live" if value > ms else "terminal")
                      for ms, value in zip(case["limits_ms"], predictions, strict=True)]
            trial[method] = dict(prediction_ns=100 * MS, action="reuse", paid_native_ns=0, live=events)
        scored = score_case(case, trial, samples)
        metrics = summarize([scored])
        self.assertEqual(metrics["native_mixed_limits"], 1)
        self.assertEqual(metrics["changed_live_verdicts"], 3)
        self.assertEqual(metrics["footprint"]["within_20pct"], 1)
        self.assertEqual(metrics["footprint"]["live_verdicts"], dict(agreement=2, mixed_native=1, false_accept=0, false_tle=0))
        self.assertEqual(metrics["temporal"]["live_verdicts"], dict(agreement=0, mixed_native=1, false_accept=1, false_tle=1))
        self.assertEqual(scored["footprint"]["live"][1]["native_exceeded"], 1)


class FootprintReplay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.output = unpack(RESULT / "output.tar.xz", Path(cls.temp.name))

    def test_reproduces_all_registered_observations(self):
        result = verify(self.output)
        self.assertEqual(result, json.loads((RESULT / "metrics.json").read_bytes()))
        collection = json.loads((RESULT / "collection.json").read_bytes())
        self.assertEqual(sha((RESULT / "output.tar.xz").read_bytes()), collection["output_sha256"])
        for name in ("plan", "rules"):
            self.assertEqual(result[name + "_sha256"],
                             collection["input_sources_sha256"][f"experiments/footprint_admission/{name}.json"])
        self.assertEqual(result["native_runs"], 117)
        self.assertEqual(result["totals"]["previous"]["inputs"], 4)
        self.assertEqual(result["totals"]["prospective"]["inputs"], 9)
        self.assertEqual(len(result["initial_cost_ns"]), 3)
        for method in METHODS:
            self.assertEqual(result["totals"]["all"][method]["limits"], 52)
            for case in result["cases"].values():
                arm = case[method]
                self.assertEqual(arm["paid_native_ns"], arm["prediction_ns"] if arm["action"] == "calibrate" else 0)
        self.assertEqual(result["pids_verified"], 16 + result["physical_native_calibrations"] + 104 + 117)

    def test_rejects_profile_with_a_different_summary_pid(self):
        path = self.output / "summary.json"
        before = path.read_bytes()
        try:
            summary = json.loads(before)
            summary["rows"][0]["profile_pid"] += 1
            path.write_text(json.dumps(summary))
            with self.assertRaisesRegex(ValueError, "wrong combined profile PID"):
                verify(self.output)
        finally:
            path.write_bytes(before)

    def test_rejects_selection_with_a_different_reference_index(self):
        self.reject_edit("cases/fenwick/large/footprint-selection.json",
                         "reference_index", 402, "selection disagrees")

    def test_rejects_live_receipt_bound_to_a_different_capture(self):
        self.reject_edit("cases/fenwick/large/limits/temporal/00/result.json",
                         "current_result_sha256", "0" * 64, "wrapper differs")

    def reject_edit(self, relative, key, replacement, message):
        path = self.output / relative
        before = path.read_bytes()
        try:
            body = json.loads(before)
            body[key] = replacement
            path.write_text(json.dumps(body))
            with self.assertRaisesRegex(ValueError, message):
                verify(self.output)
        finally:
            path.write_bytes(before)
