"""Independently compare the bounded deadline journal with the published CPU-1 measurements."""
from collections import Counter, defaultdict
from fractions import Fraction
from hashlib import sha256
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
from scrivener_cache.score import fraction_in_range
from scrivener_lazy.verify import verify


def classifications(limits, estimate, times):
    result = Counter()
    for ms in limits:
        limit = ms * 1_000_000
        if estimate <= limit < median(times):
            result["majority_false_pass"] += 1
        elif median(times) <= limit < estimate:
            result["majority_false_timeout"] += 1
        else:
            result["majority_correct"] += 1
        if min(times) <= limit < max(times):
            result["mixed"] += 1
        elif estimate <= limit < min(times):
            result["unanimous_false_pass"] += 1
        elif max(times) <= limit < estimate:
            result["unanimous_false_timeout"] += 1
        else:
            result["unanimous_correct"] += 1
    return result


class ScrivenerLazyReplay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        cls.plan_raw = (EXPERIMENTS / "scrivener_lazy/plan.json").read_bytes()
        cls.plan = json.loads(cls.plan_raw)
        cls.boots = []
        for suffix in ("", "-repeat"):
            directory = EXPERIMENTS / "results" / ("gce-scrivener-lazy" + suffix)
            root = unpack(directory / "output.tar.gz", Path(tmp.name) / directory.name)
            cls.boots.append((directory, root, load(root / "run.json"), load(root / "predictions.json"), verify(root)))

    def test_both_independent_boots_match_public_metrics_and_provider_receipts(self):
        freezes, boots = [], []
        for directory, _, run, seal, report in self.boots:
            self.assertEqual(report, load(directory / "metrics.json"))
            receipt = load(directory / "collection.json")
            self.assertEqual(receipt["output_sha256"], sha256((directory / "output.tar.gz").read_bytes()).hexdigest())
            self.assertEqual(receipt["freeze"]["plan_sha256"], sha256(self.plan_raw).hexdigest())
            platform, cleanup = receipt["platform"], receipt["cleanup"]
            self.assertEqual((platform["cpu_platform"], platform["machine_type"], platform["threads_per_core"]),
                             ("AMD Milan", "n2d-standard-4", 1))
            self.assertEqual((cleanup["instance_http"], cleanup["boot_disk_http"], cleanup["key_removed"]),
                             (404, 404, True))
            freezes.append(receipt["freeze"])
            boots.append(report["boot_sha256"])
            self.assertEqual(len(seal["calls"]), 48)
            rows, cutoff = run["rows"], report["sealed_prefix_rows"]
            self.assertEqual(report["verified_pids"], len(rows))
            self.assertEqual(set(r["lane"] for r in rows[cutoff:]), {"validation"})
            self.assertEqual(Counter(r["arm"] for r in rows[cutoff:]), {"native": 108})
            self.assertEqual(Counter(r["arm"] for r in rows[:42]), {"native": 36, "branch": 4, "scan": 2})
            self.assertEqual({r["name"] for r in rows[:42]}, set(self.plan["anchors"]))
            self.assertTrue(all(r["arm"] in ("scan", "branch") and r["lane"] in ("lazy", "eager") for r in rows[42:cutoff]))
            orders = defaultdict(list)
            for i in range(0, 48, 2):
                a, b = seal["calls"][i:i + 2]
                self.assertEqual([a[k] for k in ("name", "program", "rep")], [b[k] for k in ("name", "program", "rep")])
                self.assertEqual({a["lane"], b["lane"]}, {"eager", "lazy"})
                orders[a["name"], a["program"]].append((a["rep"], a["lane"]))
            self.assertEqual(len(orders), 12)
            for first, second in orders.values():
                self.assertEqual((first[0], second[0]), (1, 2))
                self.assertNotEqual(first[1], second[1])
        self.assertEqual(*freezes)
        self.assertNotEqual(*boots)

    def test_paired_costs_reconcile_to_child_wait4_and_controller_thread_time(self):
        for _, _, run, seal, report in self.boots:
            measured = report["summary"]["measured"]
            totals = {}
            for lane in ("eager", "lazy"):
                calls = [c for c in seal["calls"] if c["lane"] == lane]
                rows = [r for r in run["rows"] if r["lane"] == lane]
                self.assertEqual(sum((c["sequence_ids"] for c in calls), []), [r["sequence"] for r in rows])
                raw = measured[lane]
                for arm in ("scan", "branch"):
                    observations = [(r["user_us"] + r["sys_us"]) for r in rows if r["arm"] == arm]
                    self.assertEqual((raw[arm + "_calls"], raw[arm + "_cpu_us"]), (len(observations), sum(observations)))
                child = sum(r["user_us"] + r["sys_us"] for r in rows)
                thread = sum(c["router_cpu_ns"] for c in calls)
                totals[lane] = child * 1000 + thread
                self.assertEqual((raw["child_cpu_us"], raw["router_cpu_ns"], raw["elapsed_ns"], raw["combined_cpu_ns"]),
                                 (child, thread, sum(c["elapsed_ns"] for c in calls), totals[lane]))
                self.assertEqual(Counter(c["decision"]["path"] for c in calls), Counter(raw["paths"]))
            self.assertEqual((measured["eager"]["scan_calls"], measured["eager"]["branch_calls"]), (24, 24))
            self.assertEqual(measured["combined_saved_ppm"],
                             round(1_000_000 * Fraction(totals["eager"] - totals["lazy"], totals["eager"])))
            self.assertEqual(measured["efficiency_gate"], (totals["lazy"] < totals["eager"] and measured["equivalent_calls"] == 24))
            for phase in ("calibration", "validation"):
                for arm, total in report["summary"][phase].items():
                    rows = [r for r in run["rows"] if (r["lane"], r["arm"]) == (phase, arm)]
                    self.assertEqual(total, {"calls": len(rows), "child_cpu_us": sum(r["cpu_us"] for r in rows)})

    def test_eager_clock_and_native_deadline_votes_for_every_pair(self):
        limits = self.plan["fixed_limits_ms"]
        for _, _, run, seal, report in self.boots:
            native, features = defaultdict(list), {}
            for row in run["rows"]:
                if row["arm"] == "native":
                    native[row["name"], row["program"]].append((row["user_us"] + row["sys_us"]) * 1000)
                elif row["lane"] in ("calibration", "eager"):
                    field = "disruptive" if row["arm"] == "scan" else "bcm"
                    features[row["name"], row["program"], row["rep"], field] = row["features"][field]
            fixed, grid = Counter(), Counter()
            errors = defaultdict(dict)
            calls = {(c["name"], c["program"], c["rep"], c["lane"]): c["decision"] for c in seal["calls"]}
            matches = 0
            for case in (c for c in self.plan["cases"] if c["group"] == "holdout"):
                name, ms = case["name"], case["fixed_limit_ms"]
                for program in self.plan["binaries"]:
                    low, high = (median(native[n, program]) for n in self.plan["anchors"])
                    entry = report["cases"][name][program]
                    times = native[name, program]
                    self.assertEqual(len(times), 9)
                    self.assertEqual(entry["native_ns"], times)
                    self.assertEqual(entry["median_native_ns"], median(times))
                    self.assertEqual(entry["native_tle_votes"], {str(x): sum(t > x * 1_000_000 for t in times) for x in limits})
                    for rep in (1, 2):
                        rates = []
                        for field, source in (("disruptive", "scan"), ("bcm", program)):
                            endpoints = [features[n, source, 1, field] for n in self.plan["anchors"]]
                            rates.append(fraction_in_range(*endpoints, features[name, program, rep, field]))
                        # Apply the previous release's normalized features, interpolate and round only once.
                        estimate = ceil(((2 - sum(rates)) * low + sum(rates) * high) / 2)
                        eager, lazy = (calls[name, program, rep, lane] for lane in ("eager", "lazy"))
                        self.assertEqual(eager["lower_ns"], estimate)
                        self.assertEqual(entry["eager_estimate_ns"][rep - 1], estimate)
                        matches += lazy["over_budget"] == (estimate > ms * 1_000_000)
                        if rep == 1:
                            pure_predictions = sorted(ceil((1 - rate) * low + rate * high) for rate in rates)
                            self.assertEqual(entry["convex_estimate_interval_ns"], pure_predictions)
                            self.assertLessEqual(pure_predictions[0], estimate)
                            self.assertGreaterEqual(pure_predictions[1], estimate)
                            midpoint = (low + high + 1) // 2
                            self.assertEqual(midpoint, entry["midpoint_ns"])
                            for method, ns in (("calibration_midpoint", midpoint), ("branch_stream", estimate)):
                                errors[method][name, program] = abs(Fraction(ns, median(times)) - 1)
                            grid.update(classifications(limits, estimate, times))
                            fixed.update(classifications([ms], ms * 1_000_000 + int(lazy["over_budget"]), times))
            summary = report["summary"]
            self.assertEqual((summary["grid_eager_comparisons"], summary["measured"]["equivalent_calls"]), (288, matches))
            for method, values in errors.items():
                worst_case = max(values, key=values.get)
                self.assertEqual(summary["descriptive_accuracy"][method],
                                 dict(cases=12, within_20pct=sum(5 * e <= 1 for e in values.values()),
                                      median_absolute_error_ppm=round(median(values.values()) * 1_000_000),
                                      worst_absolute_error_ppm=round(max(values.values()) * 1_000_000),
                                      worst_case=list(worst_case)))
            for category, count, expected in (("grid", 144, grid), ("fixed", 12, fixed)):
                result = dict(summary[category])
                self.assertEqual(result.pop("pairs_or_limits"), count)
                self.assertEqual(Counter(result), expected)

    def test_detects_modified_missing_and_unregistered_raw_evidence(self):
        _, root, run, _, _ = self.boots[0]
        row = next(r for r in run["rows"] if r["arm"] == "branch")
        path = root / "runs" / f"{row['sequence']:03d}" / "cachegrind.out"
        original = path.read_bytes()
        try:
            path.write_bytes(original + b"\n")
            with self.assertRaises(ValueError):
                verify(root)
            path.unlink()
            with self.assertRaises(FileNotFoundError):
                verify(root)
        finally:
            path.write_bytes(original)
        unexpected = path.parent / "unregistered-scan.out"
        try:
            unexpected.write_text("0 0 0 0\n")
            with self.assertRaisesRegex(ValueError, "unexpected file"):
                verify(root)
        finally:
            unexpected.unlink()
        trace = root / "exec-trace"
        original = trace.read_bytes()
        try:
            trace.write_bytes(original + b"\nscan [001] .... 9.000: sched_process_exec: filename=/payload/binaries/scan pid=987654321 old_pid=987654321\n")
            with self.assertRaisesRegex(ValueError, "unregistered measured process"):
                verify(root)
        finally:
            trace.write_bytes(original)

    def test_rejects_rehashed_seal_with_impossible_lazy_decision(self):
        _, root, _, _, _ = self.boots[0]
        seal_path, run_path = root / "predictions.json", root / "run.json"
        seal_raw, run_raw = seal_path.read_bytes(), run_path.read_bytes()
        try:
            seal, run = json.loads(seal_raw), json.loads(run_raw)
            lazy = next(c["decision"] for c in seal["calls"] if c["lane"] == "lazy")
            lazy["over_budget"] = not lazy["over_budget"]
            changed = json.dumps(seal).encode()
            seal_path.write_bytes(changed)
            run["predictions_sha256"] = sha256(changed).hexdigest()
            run_path.write_bytes(json.dumps(run).encode())
            with self.assertRaisesRegex(ValueError, "independent bounded replay"):
                verify(root)
        finally:
            run_path.write_bytes(run_raw)
            seal_path.write_bytes(seal_raw)
