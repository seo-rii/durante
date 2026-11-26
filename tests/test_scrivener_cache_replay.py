"""Replay both CPU-pinned cache studies and try to falsify their forecast and collector receipts."""
from fractions import Fraction
from hashlib import sha256
from itertools import groupby
import json
from math import ceil
from pathlib import Path
from statistics import median
import sys
from tempfile import TemporaryDirectory
import unittest

from test_scrivener_order_replay import unpack

EXPERIMENTS = Path(__file__).resolve().parents[1] / "experiments"
sys.path.insert(0, str(EXPERIMENTS))
from cpp20_locality.export import parse_trace
from scrivener_cache.verify import verify

ENDPOINTS = {"ascending", "shuffled"}
HOLDOUTS = {"tiles32", "tiles4096", "reverse_tiles"}
PROGRAMS = {"scrivener", "scrivener_lifting"}


def load(path):
    return json.loads(path.read_bytes())


def native_median(rows, name, program):
    return 1000 * median(r["user_us"] + r["sys_us"] for r in rows
                         if (r["name"], r["program"], r["arm"]) == (name, program, "native"))


def read_misses(root, rows, name, program):
    row = next(r for r in rows if (r["name"], r["program"], r["arm"]) == (name, program, "cachegrind"))
    path = root / "runs" / f"{row['sequence']:03d}" / "cachegrind.out"
    lines = path.read_text().splitlines()
    fields = next(line.split()[1:] for line in lines if line.startswith("events:"))
    return int(lines[-1].split()[1:][fields.index("DLmr")])


class ScrivenerCacheReplay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        cls.boots = []
        for suffix in ("", "-repeat"):
            result = EXPERIMENTS / "results" / ("gce-scrivener-cache" + suffix)
            archive = result / "output.tar.gz"
            root = unpack(archive, Path(tmp.name) / result.name)
            cls.boots.append((archive, root, verify(root)))

    def test_archives_metrics_and_independent_boots(self):
        plan = EXPERIMENTS / "scrivener_cache/plan.json"
        receipts = []
        for archive, root, report in self.boots:
            self.assertEqual(report, load(archive.parent / "metrics.json"))
            receipt = load(archive.parent / "collection.json")
            receipts.append(receipt)
            self.assertEqual(receipt["output_sha256"], sha256(archive.read_bytes()).hexdigest())
            self.assertEqual(receipt["freeze"]["plan_sha256"], sha256(plan.read_bytes()).hexdigest())
            platform, cleanup = receipt["platform"], receipt["cleanup"]
            self.assertEqual((platform["machine_type"], platform["cpu_platform"], platform["threads_per_core"]),
                             ("n2d-standard-4", "AMD Milan", 1))
            self.assertEqual((cleanup["instance_http"], cleanup["boot_disk_http"], cleanup["key_removed"]),
                             (404, 404, True))
            run = load(root / "run.json")
            self.assertEqual((run["placement"]["cpu"], run["cgroup_allowed_cpus"], report["verified_pids"]),
                             (1, "1", 110))
        self.assertEqual(receipts[0]["freeze"], receipts[1]["freeze"])
        self.assertNotEqual(self.boots[0][2]["boot_sha256"], self.boots[1][2]["boot_sha256"])

    def test_prelabel_journal_and_primary_forecast(self):
        for _, root, report in self.boots:
            rows = load(root / "run.json")["rows"]
            seal = load(root / "predictions.json")
            pids = [row["launcher_pid"] for row in rows]
            measured = set(pids)
            _, trace = parse_trace((root / "exec-trace").read_text())
            self.assertEqual([pid for pid, _ in groupby(pid for pid, _ in trace if pid in measured)], pids)
            combos = [(n, p) for n in ENDPOINTS | HOLDOUTS for p in PROGRAMS]
            self.assertCountEqual([(r["name"], r["program"], r["arm"]) for r in rows[:20]],
                                  [(n, p, a) for n, p in combos for a in ("ordinary", "cachegrind")])
            cutoff = report["sealed_prefix_rows"]
            self.assertEqual(cutoff, 56)
            for names, phase in ((ENDPOINTS, rows[20:cutoff]), (HOLDOUTS, rows[cutoff:])):
                self.assertCountEqual([(r["name"], r["program"], r["arm"], r["rep"]) for r in phase],
                                      [(n, p, "native", i) for n in names for p in PROGRAMS for i in range(1, 10)])
            prefix = json.dumps(rows[:cutoff], sort_keys=True, separators=(",", ":")).encode()
            self.assertEqual(seal["prefix_sha256"], sha256(prefix).hexdigest())
            errors = []
            for program in PROGRAMS:
                a, b = (native_median(rows, name, program) for name in ("ascending", "shuffled"))
                low, high = (read_misses(root, rows, name, program) for name in ("ascending", "shuffled"))
                for name in HOLDOUTS:
                    misses = read_misses(root, rows, name, program)
                    ratio = Fraction(misses - low, high - low) if low != high else Fraction(1, 2)
                    prediction = ceil(a + (b - a) * max(0, min(1, ratio)))
                    self.assertEqual(prediction, seal["cases"][name][program]["ll_read_misses"])
                    observed = native_median(rows, name, program)
                    errors.append(abs(Fraction(prediction - observed, observed)))
            self.assertEqual(round(1_000_000 * median(errors)),
                             report["summary"]["ll_read_misses"]["median_absolute_error_ppm"])

    def test_rehashed_forecast_change_is_rejected(self):
        with TemporaryDirectory() as tmp:
            root = unpack(self.boots[0][0], Path(tmp))
            path = root / "predictions.json"
            seal = load(path)
            seal["cases"]["tiles32"]["scrivener"]["ll_read_misses"] += 1
            path.write_text(json.dumps(seal))
            journal = root / "run.json"
            run = load(journal)
            run["predictions_sha256"] = sha256(path.read_bytes()).hexdigest()
            journal.write_text(json.dumps(run))
            with self.assertRaisesRegex(ValueError, "differs from sealed forecast"):
                verify(root)

    def test_rehashed_cachegrind_log_with_wrong_version_is_rejected(self):
        with TemporaryDirectory() as tmp:
            root = unpack(self.boots[0][0], Path(tmp))
            journal = root / "run.json"
            run = load(journal)
            row = next(r for r in run["rows"] if r["arm"] == "cachegrind")
            path = root / "runs" / f"{row['sequence']:03d}" / "cachegrind.log"
            path.write_bytes(path.read_bytes().replace(b"Valgrind-3.19.0", b"Valgrind-9.99.9"))
            row["cachegrind_log_sha256"] = sha256(path.read_bytes()).hexdigest()
            journal.write_text(json.dumps(run))
            with self.assertRaisesRegex(ValueError, "wrong Cachegrind version"):
                verify(root)


if __name__ == "__main__":
    unittest.main()
