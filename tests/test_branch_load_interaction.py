"""Regression checks for the calibration and blind evaluation split."""
import copy
from fractions import Fraction
from hashlib import sha256
import json
from importlib.util import find_spec
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments"))
import branch_load_interaction as model


def case(name, rows):
    digest = sha256(name.encode()).hexdigest()
    return {"elf_sha256": digest, "input_sha256": digest, "output_sha256": digest,
            "counters": [dict(zip(model.COUNTERS, values)) for values in rows]}


def result_path(name):
    return Path(__file__).resolve().parents[1] / "experiments/results" / name


class PublishedCounterexampleTests(unittest.TestCase):
    def test_monotone_features_have_wrong_native_order_even_at_extremes(self):
        data = json.loads(result_path("cpp20-v8-training.json").read_bytes())
        labels = json.loads(result_path("cpp20-v8-labels.json").read_bytes())["cases"]
        linked = data["external"]["linked-rank"]
        branch = data["training"]["branch-2"]
        self.assertEqual(model.identity(linked), model.identity(labels["linked-rank"]))
        small, large = model.read_samples(linked), model.read_samples(branch)
        for key in (*model.COUNTERS, model.INTERACTION):
            self.assertLessEqual(max(r[key] for r in small), min(r[key] for r in large), key)
        # A nondecreasing estimator gives P(linked) <= P(branch). For ±e
        # agreement at even the nearest native times, (1-e)L <= (1+e)B.
        fastest_linked = min(labels["linked-rank"]["native_ns"])
        slowest_branch = max(branch["native_ns"])
        self.assertEqual((fastest_linked, slowest_branch), (177544000, 99647000))
        lower_bound = Fraction(fastest_linked - slowest_branch, fastest_linked + slowest_branch)
        self.assertEqual(lower_bound, Fraction(77897, 277191))
        self.assertGreater(lower_bound, Fraction(1, 5))


class InteractionTests(unittest.TestCase):
    def setUp(self):
        a = case("a", [(100, 0, 0, 120, 4, 4)] * 3)
        b = case("b", [(120, 0, 300, 140, 8, 2)] * 3)
        self.ext = case("ext", [(100, 0, 0, 100, 8, 10)] * 3)
        self.data = {"schema": 1, "training": {"a": {**a, "family": "a", "native_ns": [203] * 6},
                                              "b": {**b, "family": "b", "native_ns": [241] * 6}},
                     "external": {"unseen": self.ext}}

    def test_holdout_rejects_timing_or_training_executable(self):
        timed = copy.deepcopy(self.data)
        timed["external"]["unseen"]["native_ns"] = [100] * 6
        with self.assertRaisesRegex(ValueError, "only identity and counters"):
            model.freeze(timed)
        self.ext["elf_sha256"] = self.data["training"]["a"]["elf_sha256"]
        with self.assertRaisesRegex(ValueError, "used for fitting"):
            model.freeze(self.data)

    def test_duplicate_case_aliases_do_not_count_as_independent_inputs(self):
        for cohort, key in (("training", "b"), ("external", "unseen")):
            with self.subTest(cohort=cohort):
                data = copy.deepcopy(self.data)
                data[cohort]["another-name"] = data[cohort][key]
                with self.assertRaisesRegex(ValueError, "duplicate"):
                    model.freeze(data)

    def test_interaction_uses_per_replay_integer_sqrt(self):
        self.assertEqual([row[model.INTERACTION] for row in model.read_samples(self.ext)], [8, 8, 8])
        self.ext["counters"][0]["load_instructions"] = 101
        with self.assertRaisesRegex(ValueError, "inconsistent counts"):
            model.read_samples(self.ext)

    @unittest.skipUnless(find_spec("scipy") and find_spec("numpy"), "fit uses SciPy and NumPy")
    def test_freeze_and_score_bind_identity_and_calibration(self):
        frozen = model.freeze(self.data)
        fold_b = frozen["models"]["interaction"]["family_exclusion"]["b"]
        self.assertIn("rep_stos_bytes", fold_b["unobserved_training_features"])
        changed = copy.deepcopy(self.data)
        for row in changed["external"]["unseen"]["counters"]:
            row["instructions"] = 9999
        other = model.freeze(changed)
        self.assertEqual(frozen["models"], other["models"])
        self.assertNotEqual(frozen["external"], other["external"])
        labels = {"schema": 1, "cases": {"unseen": {**model.identity(self.ext),
                                                      "native_ns": [10_000_000] * 6}}}
        self.assertEqual(model.score(frozen, labels)["accuracy"]["interaction"]["predictions"], 3)
        for bad in ({}, {k: -1 for k in model.COUNTERS}, {k: float("inf") for k in model.COUNTERS}):
            with self.subTest(bad=bad):
                invalid = copy.deepcopy(frozen)
                invalid["external"]["unseen"]["counter_spread"] = bad
                with self.assertRaisesRegex(ValueError, "counter spreads"):
                    model.score(invalid, labels)
        labels["cases"]["unseen"]["input_sha256"] = "a" * 64
        with self.assertRaisesRegex(ValueError, "mismatched artifact"):
            model.score(frozen, labels)
        labels["cases"].clear()
        with self.assertRaisesRegex(ValueError, "label set"):
            model.score(frozen, labels)
