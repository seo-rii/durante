from functools import partial
from hashlib import sha256
from itertools import product
import json
from pathlib import Path
import sys
import unittest

EXPERIMENTS = Path(__file__).resolve().parents[1] / "experiments"
sys.path.insert(0, str(EXPERIMENTS))
from scrivener_lazy import corpus, model
from scrivener_streams.score import forecasts


def anchor(t, stream, branch):
    return dict(native_ns=t, disruptive=stream, bcm=branch, position_bits=1, work=1, misses=0)


class BoundedDecision(unittest.TestCase):
    def test_all_rounding_edges_against_original_clock(self):
        for (a, b), (s0, s1), (b0, b1) in product(
                ((11, 18), (18, 11), (13, 13), (2**55 + 1, 2**55 + 8)),
                ((2, 5), (5, 2), (4, 4)), ((1, 5), (5, 1), (3, 3))):
            low, high = anchor(a, s0, b0), anchor(b, s1, b1)
            anchors = dict(ascending=low, shuffled=high)
            for s, bc in product(range(0, 7), range(0, 7)):
                case = dict(disruptive=s, bcm=bc, position_bits=1, work=1, misses=0)
                exact = forecasts(a, b, low, high, case)["branch_stream"]
                for limit in {min(a,b)-1, exact-1, exact, exact+1, max(a,b)}:
                    calls = []
                    def count(name, value):
                        calls.append(name)
                        return value
                    lazy = model.decide(anchors, limit, partial(count, "scan", s), partial(count, "branch", bc))
                    self.assertEqual(lazy["over_budget"], exact > limit)
                    self.assertLessEqual(lazy["lower_ns"], exact)
                    self.assertLessEqual(exact, lazy["upper_ns"])
                    self.assertTrue(lazy["upper_ns"] <= limit or lazy["lower_ns"] > limit)
                    self.assertEqual(calls, {"anchors": [], "scan": ["scan"], "branch": ["scan","branch"]}[lazy["path"]])
                eager = model.decide(anchors, exact, lambda: s, lambda: bc, eager=True)
                self.assertEqual(eager, dict(path="branch", over_budget=False, lower_ns=exact, upper_ns=exact))

    def test_paths_and_counter_failures(self):
        anchors = dict(ascending=anchor(10, 2, 2), shuffled=anchor(30, 12, 12))
        def unused():
            self.fail("unnecessary counter ran")
        self.assertEqual(model.decide(anchors, 30, unused, unused)["path"], "anchors")
        self.assertEqual(model.decide(anchors, 20, lambda: 0, unused)["path"], "scan")
        with self.assertRaisesRegex(ValueError, "counter"):
            model.decide(anchors, 20, lambda: -1, unused)
        with self.assertRaisesRegex(ValueError, "budget"):
            model.decide(anchors, True, unused, unused)
        with self.assertRaises(OSError):
            model.decide(anchors, 20, lambda: 12, partial(self._failed_measurement))

    @staticmethod
    def _failed_measurement():
        raise OSError("measurement failed")


class FreshCorpus(unittest.TestCase):
    def test_registered_inputs_and_exogenous_budgets(self):
        p = json.loads((EXPERIMENTS / "scrivener_lazy/plan.json").read_bytes())
        current = {c["name"]:c for c in p["cases"]}
        old_hashes = set()
        for study in ("scrivener_order","scrivener_cache","scrivener_pages","scrivener_streams"):
            old = json.loads((EXPERIMENTS / study / "plan.json").read_bytes())
            old_hashes.update(c["stdin_sha256"] for c in old["cases"])
        names = sorted(corpus.NAMES[2:], key=lambda n: sha256(("scrivener-lazy-v1/" + n).encode()).digest())
        self.assertEqual([current[n]["fixed_limit_ms"] for n in names], [30,40,45,50,60,75])
        self.assertFalse(old_hashes.intersection(current[n]["stdin_sha256"] for n in names))
        text, expected = corpus.make("ascending", 2309)
        prefix, queries = text.splitlines()[1:2310], text.splitlines()[2310:]
        for name in names:
            text, out = corpus.make(name, 2309)
            lines = text.splitlines()[1:]
            self.assertEqual(lines[:2309], prefix)
            self.assertCountEqual(lines[2309:], queries)
            self.assertEqual(out.splitlines(), [prefix[int(q.split()[1])].split()[1] for q in lines[2309:]])
