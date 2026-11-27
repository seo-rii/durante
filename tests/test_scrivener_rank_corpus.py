"""Verify the order oracle and the age and displacement of same-page revisits."""
import json
from pathlib import Path
from random import Random
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from test_scrivener_streams_corpus import expected_counts, input_for

EXPERIMENTS = Path(__file__).resolve().parents[1] / "experiments"
sys.path.insert(0, str(EXPERIMENTS))
from scrivener_rank import corpus
from scrivener_streams import corpus as streams


def revisit_counts(positions):
    # Inspect the actual intervening sequence, independently of the bounded LRU.
    latest = {}
    distance = stale = 0
    for index, position in enumerate(positions):
        page = position // 512
        if page not in latest:
            stale += 1
        else:
            previous = latest[page]
            between = positions[previous + 1:index]
            delta = abs(position - positions[previous])
            if delta > 8 and len({p // 512 for p in between}) >= 16:
                distance += delta
                stale += len(between) >= 128
        latest[page] = index
    return distance, stale


class ScrivenerRankCorpus(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        directory = TemporaryDirectory()
        cls.addClassCleanup(directory.cleanup)
        cls.scanner = Path(directory.name) / "scan"
        subprocess.run(["g++", "-std=c++20", "-O2", "-DSCRIVENER_RANK_FEATURES",
                        EXPERIMENTS / "scrivener_streams/scan.cpp", "-o", cls.scanner], check=True)

    def check_positions(self, positions):
        text = input_for(positions) if positions else "0\n"
        expected = (*expected_counts(positions), *revisit_counts(positions))
        self.assertEqual(tuple(corpus.features(text).values()), expected)
        output = subprocess.run([self.scanner], input=text, text=True, capture_output=True, check=True)
        self.assertEqual(tuple(map(int, output.stdout.split())), expected)
        return expected

    def test_count_intervening_queries_and_refresh_even_on_hit(self):
        other_pages = [512 * (1 + i % 16) for i in range(128)]
        self.assertEqual(self.check_positions([0, *other_pages[:-1], 9])[-2:], (9, 17))
        self.assertEqual(self.check_positions([0, *other_pages, 9])[-2:], (9, 18))
        self.assertEqual(self.check_positions([0, *other_pages, 8])[-2:], (0, 17))
        self.assertEqual(self.check_positions([0, *other_pages, 8, 9, *other_pages, 17])[-2:], (0, 17))
        self.check_positions([])
        rng = Random(9231)
        self.check_positions([rng.randrange(10000) for _ in range(450)])

    def test_all_orders_preserve_the_document_queries_and_output(self):
        size = 631
        asc, _ = streams.make("ascending", size)
        initial = asc.splitlines()[1:size + 1]
        positions = asc.splitlines()[size + 1:]
        for name in corpus.NAMES:
            with self.subTest(name=name):
                text, answer = corpus.make(name, size)
                lines = text.splitlines()
                self.assertEqual(lines[1:size + 1], initial)
                self.assertCountEqual(lines[size + 1:], positions)
                self.assertEqual(answer.splitlines(),
                                 [initial[int(q.split()[1])].split()[1] for q in lines[size + 1:]])

    def test_controls_reuse_prior_inputs_and_fresh_cases_have_unique_bytes(self):
        root = EXPERIMENTS
        plan = json.loads((root / "scrivener_rank/plan.json").read_text())
        previous = {}
        for study in ("scrivener_order", "scrivener_cache", "scrivener_pages", "scrivener_streams", "scrivener_lazy"):
            for case in json.loads((root / study / "plan.json").read_text())["cases"]:
                previous[case["name"]] = case
        current = {c["name"]: c for c in plan["cases"]}
        self.assertEqual(set(current), set(corpus.NAMES))
        for name in (*plan["anchors"], *corpus.CONTROLS):
            self.assertEqual(current[name]["stdin_sha256"], previous[name]["stdin_sha256"])
            self.assertEqual(current[name]["stdout_sha256"], previous[name]["stdout_sha256"])
        historical = {c["stdin_sha256"] for c in previous.values()}
        new = {current[n]["stdin_sha256"] for n in corpus.FRESH}
        self.assertEqual(len(new), 8)
        self.assertTrue(historical.isdisjoint(new))
