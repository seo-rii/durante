"""Independently check the two published page-order trials and their deadline comparisons."""
from collections import Counter
from hashlib import sha256
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

from test_scrivener_order_replay import load, unpack

EXPERIMENTS = Path(__file__).resolve().parents[1] / "experiments"
sys.path.insert(0, str(EXPERIMENTS))
from scrivener_pages.verify import verify


class ScrivenerPagesReplay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        cls.boots = []
        for tag in ("gce-scrivener-pages", "gce-scrivener-pages-repeat"):
            published = EXPERIMENTS / "results" / tag
            output = unpack(published / "output.tar.gz", Path(tmp.name) / tag)
            cls.boots.append((published, load(output / "run.json"), verify(output)))

    def test_identical_payload_independent_boots_and_receipts(self):
        freezes, boots = [], []
        for published, run, replay in self.boots:
            self.assertEqual(replay, load(published / "metrics.json"))
            collection = load(published / "collection.json")
            self.assertEqual(collection["output_sha256"], sha256((published / "output.tar.gz").read_bytes()).hexdigest())
            self.assertEqual(collection["freeze"]["plan_sha256"],
                             sha256((EXPERIMENTS / "scrivener_pages/plan.json").read_bytes()).hexdigest())
            platform, cleanup = collection["platform"], collection["cleanup"]
            self.assertEqual((platform["cpu_platform"], platform["machine_type"], platform["threads_per_core"]),
                             ("AMD Milan", "n2d-standard-4", 1))
            self.assertEqual((cleanup["instance_http"], cleanup["boot_disk_http"], cleanup["key_removed"]),
                             (404, 404, True))
            self.assertEqual(Counter(r["arm"] for r in run["rows"]), dict(scan=21, ordinary=28, branch=28, native=126))
            self.assertTrue(all(r["arm"] == "native" and r["name"] not in ("ascending", "shuffled")
                                for r in run["rows"][113:]))
            self.assertTrue(all(r["arm"] != "native" or r["name"] in ("ascending", "shuffled")
                                for r in run["rows"][:113]))
            freezes.append(collection["freeze"])
            boots.append(run["placement"]["boot_sha256"])
        self.assertEqual(*freezes)
        self.assertNotEqual(*boots)

    def test_unanimous_errors_from_raw_cpu_time_and_sealed_estimates(self):
        for _, run, replay in self.boots:
            for method, summary in replay["summary"].items():
                false_pass = false_timeout = 0
                for name, programs in replay["cases"].items():
                    if name in ("ascending", "shuffled"):
                        continue
                    for program, case in programs.items():
                        times = [1000 * (r["user_us"] + r["sys_us"]) for r in run["rows"]
                                 if r["arm"] == "native" and r["name"] == name and r["program"] == program]
                        self.assertEqual(len(times), 9)
                        predicted = case["predictions_ns"][method]
                        for ms in range(30, 86, 5):
                            limit = ms * 1_000_000
                            false_pass += predicted <= limit < min(times)
                            false_timeout += max(times) <= limit < predicted
                self.assertEqual((false_pass, false_timeout),
                                 (summary["unanimous_false_pass"], summary["unanimous_false_timeout"]))
