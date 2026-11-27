"""Replay the sealed IOI Wall range forecasts and the separately executed page probe."""
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
from cpp20_locality.export import parse_trace
from wall_locality.verify import verify

ANCHORS = ("ascending", "shuffled")
HOLDOUTS = ("tiles128", "tiles8192", "stride64", "page_round_robin", "shuffled_tiles")
ARMS = ("ordinary", "full", "branch_only", "leaf_pages")


def prefix_hash(rows):
    return sha256(json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class WallLocalityReplay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        cls.boots = []
        for suffix in ("", "-repeat"):
            result = EXPERIMENTS / "results" / ("gce-wall-locality" + suffix)
            archive = result / "output.tar.gz"
            root = unpack(archive, Path(tmp.name) / result.name)
            cls.boots.append((archive, root, verify(root)))

    def test_metrics_replay_and_independent_guests(self):
        raw = (EXPERIMENTS / "wall_locality/plan.json").read_bytes()
        freezes, boots = [], []
        for archive, root, report in self.boots:
            self.assertEqual(report, load(archive.parent / "metrics.json"))
            receipt = load(archive.parent / "collection.json")
            self.assertEqual(receipt["output_sha256"], sha256(archive.read_bytes()).hexdigest())
            self.assertEqual(receipt["freeze"]["plan_sha256"], sha256(raw).hexdigest())
            place, cleanup = receipt["platform"], receipt["cleanup"]
            self.assertEqual((place["cpu_platform"], place["machine_type"], place["threads_per_core"]),
                             ("AMD Milan", "n2d-standard-4", 1))
            self.assertEqual((cleanup["instance_http"], cleanup["boot_disk_http"], cleanup["key_removed"]),
                             (404, 404, True))
            run = load(root / "run.json")
            self.assertEqual((run["placement"]["cpu"], run["cgroup_allowed_cpus"], report["verified_pids"]),
                             (1, "1", 175))
            freezes.append(receipt["freeze"])
            boots.append(run["placement"]["boot_sha256"])
        self.assertEqual(*freezes)
        self.assertNotEqual(*boots)

    def test_capture_order_and_sealed_cutoff(self):
        for _, root, report in self.boots:
            rows = load(root / "run.json")["rows"]
            cutoff = report["sealed_prefix_rows"]
            self.assertEqual((len(rows), cutoff), (175, 130))
            pids = [r["launcher_pid"] for r in rows]
            measured = set(pids)
            _, events = parse_trace((root / "exec-trace").read_text())
            self.assertEqual([pid for pid, _ in groupby(pid for pid, _ in events if pid in measured)], pids)
            for name in ANCHORS + HOLDOUTS:
                rounds = []
                for rep in range(1, 5):
                    block = rows[(rep - 1) * 28:rep * 28]
                    arms = [r["arm"] for r in block if r["name"] == name and r["rep"] == rep]
                    self.assertCountEqual(arms, ARMS)
                    rounds.append(arms)
                for column in zip(*rounds, strict=True):
                    self.assertCountEqual(column, ARMS)
            for names, part in ((ANCHORS, rows[112:cutoff]), (HOLDOUTS, rows[cutoff:])):
                self.assertCountEqual([(r["arm"], r["name"], r["rep"]) for r in part],
                                      [("native", n, rep) for n in names for rep in range(1, 10)])
            seal = load(root / "predictions.json")
            self.assertEqual(prefix_hash(rows[:cutoff]), seal["prefix_sha256"])
            self.assertEqual(set(seal["cases"]), set(HOLDOUTS))

    def test_joint_predictions_from_raw_profiles(self):
        for _, root, report in self.boots:
            rows = load(root / "run.json")["rows"]
            seal = load(root / "predictions.json")
            native, branch, pages = {}, {}, {}
            for name in ANCHORS + HOLDOUTS:
                def first_receipt(arm, filename):
                    row = next(r for r in rows if r["arm"] == arm and r["name"] == name)
                    self.assertEqual(row["rep"], 1)
                    return root / "runs" / f"{row['sequence']:03d}" / filename

                cg = first_receipt("branch_only", "cachegrind.out").read_text().splitlines()
                fields = next(x.split()[1:] for x in cg if x.startswith("events:"))
                self.assertCountEqual(fields, ("Ir", "Bc", "Bcm", "Bi", "Bim"))
                branch[name] = int(cg[-1].split()[1:][fields.index("Bcm")])
                accesses, pages[name] = map(int, first_receipt("leaf_pages", "leaf_pages.out").read_bytes().split())
                self.assertEqual(accesses, 262_130)
                native[name] = 1000 * median(r["user_us"] + r["sys_us"] for r in rows
                                             if r["name"] == name and r["arm"] == "native")
            a, b = (native[n] for n in ANCHORS)
            errors = []
            for name in HOLDOUTS:
                fractions = []
                for feature in (branch, pages):
                    lo, hi = (feature[n] for n in ANCHORS)
                    ratio = Fraction(feature[name] - lo, hi - lo) if hi != lo else Fraction(1, 2)
                    fractions.append(min(1, max(0, ratio)))
                estimate = ceil(a + max(fractions) * (b - a))
                self.assertEqual(estimate, seal["cases"][name]["wall"]["branch_pages"])
                errors.append(abs(Fraction(estimate - native[name], native[name])))
            result = report["summary"]["branch_pages"]
            self.assertEqual(result["median_absolute_error_ppm"], round(1_000_000 * median(errors)))
            self.assertEqual(result["worst_absolute_error_ppm"], round(1_000_000 * max(errors)))

    def test_paired_deadline_changes_from_wait4_observations(self):
        budgets = load(EXPERIMENTS / "wall_locality/plan.json")["fixed_limits_ms"]
        for _, root, report in self.boots:
            rows = load(root / "run.json")["rows"]
            seal = load(root / "predictions.json")["cases"]
            changes = {kind: [] for kind in ("false_pass_fixed", "false_pass_added",
                                              "false_timeout_fixed", "false_timeout_added")}
            for name in HOLDOUTS:
                actual = [1000 * (r["user_us"] + r["sys_us"]) for r in rows
                          if r["name"] == name and r["arm"] == "native"]
                forecasts = seal[name]["wall"]
                for milliseconds in budgets:
                    limit = milliseconds * 1_000_000
                    if min(actual) > limit:
                        first_wrong = forecasts["branch_misses"] <= limit
                        second_wrong = forecasts["branch_pages"] <= limit
                        kind = "false_pass"
                    elif max(actual) <= limit:
                        first_wrong = forecasts["branch_misses"] > limit
                        second_wrong = forecasts["branch_pages"] > limit
                        kind = "false_timeout"
                    else:
                        continue
                    if first_wrong != second_wrong:
                        direction = "fixed" if first_wrong else "added"
                        changes[kind + "_" + direction].append([name, milliseconds])
            self.assertEqual(report["unanimous_changes"], changes)

    def test_rehashed_probe_change_is_rejected(self):
        with TemporaryDirectory() as tmp:
            root = unpack(self.boots[0][0], Path(tmp))
            run_file, seal_file = root / "run.json", root / "predictions.json"
            run, seal = load(run_file), load(seal_file)
            row = next(r for r in run["rows"] if r["arm"] == "leaf_pages")
            path = root / "runs" / f"{row['sequence']:03d}" / "leaf_pages.out"
            accesses, misses = map(int, path.read_bytes().split())
            path.write_text(f"{accesses} {misses + 1}\n")
            row["stdout_sha256"] = sha256(path.read_bytes()).hexdigest()
            seal["prefix_sha256"] = prefix_hash(run["rows"][:130])
            seal_file.write_text(json.dumps(seal))
            run["predictions_sha256"] = sha256(seal_file.read_bytes()).hexdigest()
            run_file.write_text(json.dumps(run))
            with self.assertRaises(ValueError):
                verify(root)

    def test_rehashed_forecast_change_is_rejected(self):
        with TemporaryDirectory() as tmp:
            root = unpack(self.boots[0][0], Path(tmp))
            run_file, seal_file = root / "run.json", root / "predictions.json"
            run, seal = load(run_file), load(seal_file)
            seal["cases"]["stride64"]["wall"]["branch_pages"] += 1
            seal_file.write_text(json.dumps(seal))
            run["predictions_sha256"] = sha256(seal_file.read_bytes()).hexdigest()
            run_file.write_text(json.dumps(run))
            with self.assertRaisesRegex(ValueError, "forecast|prediction"):
                verify(root)


if __name__ == "__main__":
    unittest.main()
