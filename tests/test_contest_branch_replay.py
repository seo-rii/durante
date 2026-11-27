"""Replay both Wall guests and recover the sealed branch forecast from the raw event totals."""
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

from test_scrivener_order_replay import load, unpack

EXPERIMENTS = Path(__file__).resolve().parents[1] / "experiments"
sys.path.insert(0, str(EXPERIMENTS))
from contest_branch.verify import verify
from cpp20_locality.export import parse_trace

ANCHORS = ("ascending", "shuffled")
HOLDOUTS = ("tiles32", "tiles4096", "bit_reversed")
ARMS = ("ordinary", "full", "branch_only")


class WallBranchReplay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        cls.boots = []
        for suffix in ("", "-repeat"):
            result = EXPERIMENTS / "results" / ("gce-wall-branch" + suffix)
            archive = result / "output.tar.gz"
            root = unpack(archive, Path(tmp.name) / result.name)
            cls.boots.append((archive, root, verify(root)))

    def test_archives_and_independent_guests(self):
        plan_sha = sha256((EXPERIMENTS / "contest_branch/plan.json").read_bytes()).hexdigest()
        freezes, boots = [], []
        for archive, root, report in self.boots:
            self.assertEqual(report, load(archive.parent / "metrics.json"))
            receipt = load(archive.parent / "collection.json")
            freezes.append(receipt["freeze"])
            self.assertEqual(receipt["freeze"]["plan_sha256"], plan_sha)
            self.assertEqual(receipt["output_sha256"], sha256(archive.read_bytes()).hexdigest())
            place, cleanup = receipt["platform"], receipt["cleanup"]
            self.assertEqual((place["cpu_platform"], place["machine_type"], place["threads_per_core"]),
                             ("AMD Milan", "n2d-standard-4", 1))
            self.assertEqual((cleanup["instance_http"], cleanup["boot_disk_http"], cleanup["key_removed"]),
                             (404, 404, True))
            journal = load(root / "run.json")
            boots.append(journal["placement"]["boot_sha256"])
            self.assertEqual((journal["placement"]["cpu"], journal["cgroup_allowed_cpus"], report["verified_pids"]),
                             (1, "1", 90))
        self.assertEqual(*freezes)
        self.assertNotEqual(*boots)

    def test_balanced_profile_rounds_and_prelabel_seal(self):
        for _, root, report in self.boots:
            rows = load(root / "run.json")["rows"]
            cutoff = report["sealed_prefix_rows"]
            self.assertEqual((len(rows), cutoff), (90, 63))
            pids = [row["launcher_pid"] for row in rows]
            measured = set(pids)
            _, events = parse_trace((root / "exec-trace").read_text())
            self.assertEqual([pid for pid, _ in groupby(pid for pid, _ in events if pid in measured)], pids)
            self.assertTrue(all(row["program"] == "wall" for row in rows))
            for name in ANCHORS + HOLDOUTS:
                rounds = []
                for rep in (1, 2, 3):
                    rows_this_round = rows[(rep - 1) * 15:rep * 15]
                    arms = [r["arm"] for r in rows_this_round if r["name"] == name and r["rep"] == rep]
                    self.assertCountEqual(arms, ARMS)
                    rounds.append(arms)
                for column in zip(*rounds, strict=True):
                    self.assertCountEqual(column, ARMS)
            for names, section in ((ANCHORS, rows[45:cutoff]), (HOLDOUTS, rows[cutoff:])):
                self.assertCountEqual([(r["arm"], r["name"], r["rep"]) for r in section],
                                      [("native", n, rep) for n in names for rep in range(1, 10)])
            seal = load(root / "predictions.json")
            prefix = json.dumps(rows[:cutoff], sort_keys=True, separators=(",", ":")).encode()
            self.assertEqual(sha256(prefix).hexdigest(), seal["prefix_sha256"])
            self.assertEqual(set(seal["cases"]), set(HOLDOUTS))

    def test_recover_branch_predictions_from_first_raw_profiles(self):
        for _, root, report in self.boots:
            rows = load(root / "run.json")["rows"]
            seal = load(root / "predictions.json")
            misses, native = {}, {}
            for name in ANCHORS + HOLDOUTS:
                row = next(r for r in rows if r["arm"] == "branch_only" and r["name"] == name)
                self.assertEqual(row["rep"], 1)
                raw = (root / "runs" / f"{row['sequence']:03d}" / "cachegrind.out").read_text().splitlines()
                fields = next(line.split()[1:] for line in raw if line.startswith("events:"))
                self.assertCountEqual(fields, ("Ir", "Bc", "Bcm", "Bi", "Bim"))
                misses[name] = int(raw[-1].split()[1:][fields.index("Bcm")])
                native[name] = 1000 * median(r["user_us"] + r["sys_us"] for r in rows
                                             if r["name"] == name and r["arm"] == "native")
            low, high = (misses[name] for name in ANCHORS)
            a, b = (native[name] for name in ANCHORS)
            errors = []
            for name in HOLDOUTS:
                ratio = Fraction(misses[name] - low, high - low) if low != high else Fraction(1, 2)
                prediction = ceil(a + max(0, min(1, ratio)) * (b - a))
                self.assertEqual(prediction, seal["cases"][name]["wall"]["branch_misses"])
                errors.append(abs(Fraction(prediction - native[name], native[name])))
            self.assertEqual(report["summary"]["branch_misses"]["median_absolute_error_ppm"],
                             round(1_000_000 * median(errors)))

    def test_rehashed_full_profile_in_branch_only_slot_is_rejected(self):
        with TemporaryDirectory() as tmp:
            root = unpack(self.boots[0][0], Path(tmp))
            journal = root / "run.json"
            run = load(journal)
            branch = next(r for r in run["rows"] if r["arm"] == "branch_only")
            full = next(r for r in run["rows"] if r["arm"] == "full" and
                        (r["name"], r["rep"]) == (branch["name"], branch["rep"]))
            src = root / "runs" / f"{full['sequence']:03d}" / "cachegrind.out"
            dst = root / "runs" / f"{branch['sequence']:03d}" / "cachegrind.out"
            dst.write_bytes(src.read_bytes())
            branch["cachegrind_sha256"] = sha256(dst.read_bytes()).hexdigest()
            journal.write_text(json.dumps(run))
            with self.assertRaisesRegex(ValueError, "wrong branch-only events or summary"):
                verify(root)

    def test_rehashed_changed_forecast_is_rejected(self):
        with TemporaryDirectory() as tmp:
            root = unpack(self.boots[0][0], Path(tmp))
            forecast = root / "predictions.json"
            seal = load(forecast)
            seal["cases"]["tiles32"]["wall"]["branch_misses"] += 1
            forecast.write_text(json.dumps(seal))
            journal = root / "run.json"
            run = load(journal)
            run["predictions_sha256"] = sha256(forecast.read_bytes()).hexdigest()
            journal.write_text(json.dumps(run))
            with self.assertRaisesRegex(ValueError, "replayed prediction differs from sealed forecast"):
                verify(root)


if __name__ == "__main__":
    unittest.main()
