"""Exact certificates for the training-only minimax diagnostic."""
from copy import deepcopy
from fractions import Fraction
from hashlib import sha256
from importlib.util import find_spec
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments"))
import memory_cost_capacity as capacity
from memory_cost_model import CANDIDATE, DIGESTS, FIELDS, LABELS, LABELS_SHA256, TRAINING, training_rows


class CertificateTests(unittest.TestCase):
    def test_two_training_feasible_fenwick_predictions(self):
        folder = LABELS.parent / "cpp20-v8-memory"
        measured = (folder / "features.json").read_bytes()
        sealed = json.loads((folder / "predictions.json").read_bytes())
        self.assertEqual(sha256(measured).hexdigest(), sealed["features_sha256"])
        originals = LABELS.read_bytes()
        self.assertEqual(sha256(originals).hexdigest(), LABELS_SHA256)
        features, labels = json.loads(measured), json.loads(originals)
        training = training_rows(features, labels)
        data = json.loads((folder / "fenwick-ambiguity.json").read_bytes())
        self.assertEqual(data["fields"], list(CANDIDATE))
        self.assertEqual(len(data["witnesses"]), 2)
        predictions = []
        for witness in data["witnesses"]:
            weights = [Fraction(w) for w in witness["weights_ns_per_unit"]]
            self.assertEqual(len(weights), len(CANDIDATE))
            self.assertGreaterEqual(min(weights), 0)

            def predict(row):
                return sum(w * row[field] for w, field in zip(weights, CANDIDATE))

            for case in training:
                times = sorted(labels["training"][case["id"]]["native_ns"])
                center = Fraction(times[2] + times[3], 2)
                for row in case["rows"]:
                    self.assertLessEqual(abs(predict(row) - center), center * Fraction(6, 100), case["id"])
            expected = Fraction(witness["fenwick_prediction_ns"])
            rows = features["external"]["fenwick-prefix"]["counters"]
            self.assertEqual([predict(row) for row in rows], [expected] * 3)
            predictions.append(expected)
        self.assertLess(min(predictions), 48_000_000)
        self.assertGreater(max(predictions), 58_000_000)

    def test_repair_and_reject_invalid_dual(self):
        upper, lower = capacity.normalized_rows([([1], 1, 1), ([1], 3, 3)])
        proof = capacity.certificate(upper, lower, [1.5], [0.25 - 1e-11, 0, 0, 0.75])
        self.assertGreater(proof["extra_units_per_upper_row"], 0)
        low, high = capacity.verify(upper, lower, proof)
        self.assertEqual(high, Fraction(1, 2))
        self.assertLessEqual(low, high)
        self.assertLess(high - low, Fraction(1, 10**9))
        proof["extra_units_per_upper_row"] = 0
        with self.assertRaisesRegex(ValueError, "dual feature"):
            capacity.verify(upper, lower, proof)


@unittest.skipUnless(find_spec("scipy"), "SciPy is needed to solve the minimax LP")
class MinimaxTests(unittest.TestCase):
    def test_range_collinearity_zero_and_separating_counter(self):
        duplicated = [1, 2, 0]
        rows = [(duplicated, 1, 1), (duplicated, 3, 3)]
        result = capacity.minimize(rows)
        lo, hi = capacity.verify(*capacity.normalized_rows(rows), result["certificate"])
        self.assertLessEqual(lo, Fraction(1, 2))
        self.assertGreaterEqual(hi, Fraction(1, 2))
        self.assertLessEqual(result["upper_error_ppb"], 500_000_001)
        self.assertGreaterEqual(result["lower_error_ppb"], 499_999_999)
        ranges = [(duplicated, 1, 2), (duplicated, 2, 4)]
        self.assertLessEqual(capacity.minimize(ranges)["upper_error_ppb"], 1)
        interior = [(duplicated, 1, 3), (duplicated, 2, 4)]
        self.assertEqual(capacity.minimize(interior)["upper_error_ppb"], 0)
        separable = [([1, 0], 1, 1), ([1, 2], 3, 3)]
        self.assertLessEqual(capacity.minimize(separable)["upper_error_ppb"], 1)
        no_features = [([0], 1, 1), ([0], 3, 3)]
        self.assertEqual(capacity.minimize(no_features)["lower_error_ppb"], 10**9)

    def test_only_original_rows_and_matching_identities(self):
        x, y = {}, {}
        for name in TRAINING:
            digest = sha256(name.encode()).hexdigest()
            ids = dict.fromkeys(DIGESTS, digest)
            vector = dict.fromkeys(FIELDS, 0)
            vector["instructions"] = 100
            x[name] = {**ids, "counters": [dict(vector) for _ in range(3)]}
            y[name] = {**ids, "native_ns": [99, 100, 101, 102, 103, 104]}
        features = {"schema": 1, "training": x, "external": object()}
        labels = {"schema": 1, "training": y, "external": object()}
        result = capacity.analyze(features, labels)
        self.assertEqual(result["case_order"], sorted(TRAINING))
        self.assertLessEqual(result["models"]["candidate"]["median"]["upper_error_ppb"], 1)
        for field in DIGESTS:
            changed = deepcopy(features)
            changed["training"]["integer-1"][field] = "0" * 64
            with self.assertRaisesRegex(ValueError, "artifacts differ"):
                capacity.analyze(changed, labels)
        changed = deepcopy(features)
        changed["training"]["integer-1"]["counters"].pop()
        with self.assertRaisesRegex(ValueError, "three feature observations"):
            capacity.analyze(changed, labels)
        incomplete = deepcopy(labels)
        incomplete["training"].pop("branch-4")
        with self.assertRaisesRegex(ValueError, "wrong training cohort"):
            capacity.analyze(features, incomplete)


if __name__ == "__main__":
    unittest.main()
