"""Check blind-input isolation and reproduce the two frozen six-counter fits."""
from copy import deepcopy
from hashlib import sha256
from importlib.util import find_spec
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

EXP = Path(__file__).resolve().parents[1] / "experiments"
sys.path.insert(0, str(EXP))
from crossed_memory_model import CALIBRATION, EXTERNAL, ORIGINAL, native_target, new_cases
from memory_cost_model import CANDIDATE

RESULTS = EXP / "results/cpp20-crossed-memory"
MANIFEST = EXP / "crossed_memory_corpus/manifest.json"


def read(path):
    return json.loads(path.read_bytes())


class CrossedMemoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.historical = read(ORIGINAL / "features.json")
        cls.features = read(RESULTS / "features.json")
        cls.labels = read(RESULTS / "calibration.json")
        cls.manifest = read(MANIFEST)

    def test_fresh_calibration_and_prediction_inputs(self):
        training, external = new_cases(self.features, self.labels, self.manifest, self.historical)
        self.assertEqual(len(training), 6)
        self.assertEqual(set(external), EXTERNAL)
        self.assertEqual(set(self.labels["cases"]), CALIBRATION)
        for row in training:
            self.assertEqual(len(row["rows"]), 3)
        self.assertEqual(len({self.features["calibration"][i]["elf_sha256"] for i in CALIBRATION}), 1)
        # Holdout outcomes cannot be introduced as auxiliary inputs to either fit.
        contaminated = deepcopy(self.features)
        contaminated["external"]["sparse-rmq"]["native_ns"] = [1] * 6
        with self.assertRaisesRegex(ValueError, "only case identity and counters"):
            new_cases(contaminated, self.labels, self.manifest, self.historical)
        labels = deepcopy(self.labels)
        labels["cases"]["cross-rep0-small"]["input_sha256"] = "1" * 64
        with self.assertRaisesRegex(ValueError, "label identity mismatch"):
            new_cases(self.features, labels, self.manifest, self.historical)

    def test_calibration_brackets(self):
        case = self.labels["cases"]["cross-rep0-small"]
        times = sorted(r[a + "_ns"] for r in case["rounds"] for a in ("native_before", "native_after"))
        self.assertEqual(native_target(case), (times[2] + times[3]) / 2)
        wrong = deepcopy(case)
        wrong["rounds"][0]["native_before"]["cpu_ns"] += 1
        with self.assertRaisesRegex(ValueError, "invalid native CPU"):
            native_target(wrong)
        wrong = deepcopy(case)
        wrong["rounds"][0]["repetition"] = 2
        with self.assertRaisesRegex(ValueError, "three ordered"):
            native_target(wrong)

    def test_integer_predictions_and_original_fit(self):
        result = read(RESULTS / "predictions.json")
        self.assertEqual(result["models"]["control"], read(ORIGINAL / "predictions.json")["models"]["candidate"])
        for name, case in self.features["external"].items():
            for arm, model in result["models"].items():
                weights = model["weights"]
                self.assertEqual(set(weights), set(CANDIDATE))
                expected = []
                for row in case["counters"]:
                    work = sum(row[k] * weights[k] for k in CANDIDATE)
                    expected.append(-(-work // result["denominator"]))
                self.assertEqual(result["external"][name]["predictions_ns"][arm], expected)

    @unittest.skipUnless(find_spec("numpy") and find_spec("scipy"), "relative-error fit uses NumPy and SciPy")
    def test_both_fits_reproduce_sealed_bytes(self):
        recorded = RESULTS / "predictions.json"
        calibration = RESULTS / "calibration.json"
        expected = read(recorded)["calibration_sha256"]
        self.assertEqual(expected, sha256(calibration.read_bytes()).hexdigest())
        with tempfile.TemporaryDirectory() as directory:
            rebuilt = Path(directory) / "predictions.json"
            subprocess.run([sys.executable, "-B", str(EXP / "crossed_memory_model.py"),
                            str(RESULTS / "features.json"), str(calibration), str(MANIFEST),
                            "--calibration-sha256", expected, "--output", str(rebuilt)], check=True)
            self.assertEqual(rebuilt.read_bytes(), recorded.read_bytes())
