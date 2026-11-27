"""Rebuild sealed Scrivener forecasts and consensus deadline changes from both guest journals."""
from collections import Counter, defaultdict
from fractions import Fraction
from hashlib import sha256
import json
from math import ceil
from pathlib import Path
import sys
from statistics import median
from tempfile import TemporaryDirectory
import unittest

from test_scrivener_lazy_replay import classifications
from test_scrivener_order_replay import load, unpack

EXPERIMENTS = Path(__file__).resolve().parents[1] / "experiments"
sys.path.insert(0, str(EXPERIMENTS))
from scrivener_rank import corpus
from scrivener_rank.verify import fixed_branch_range, verify

MS = 1_000_000
FIELDS = ("accesses", "misses", "disruptive", "position_bits", "displacement", "stale_disruptive")
FILTERS = {"branch_stale": "stale_disruptive", "branch_displacement": "displacement"}


def raw_samples(output, rows, programs):
    counters = defaultdict(dict)
    times = defaultdict(list)
    for row in rows:
        name, program = row["name"], row["program"]
        if row["arm"] == "native":
            times[name, program].append(1000 * (row["user_us"] + row["sys_us"]))
            continue
        if row["rep"] != 1:
            continue
        directory = output / "runs" / f"{row['sequence']:03d}"
        if row["arm"] == "scan":
            data = (directory / "scan.out").read_bytes().split()
            features = dict(zip(FIELDS, map(int, data), strict=True))
            for program in programs:
                counters[name, program].update(features)
        elif row["arm"] == "branch":
            profile = (directory / "cachegrind.out").read_text().splitlines()
            fields, = (line.split()[1:] for line in profile if line.startswith("events:"))
            counts, = (line.split()[1:] for line in profile if line.startswith("summary:"))
            counters[name, program]["bcm"] = int(counts[fields.index("Bcm")])
        else:
            record, = (directory / "records").glob("*.jsonl")
            terminal = json.loads(record.read_bytes().splitlines()[-1])
            counters[name, program]["work"] = terminal["instructions"] + terminal["rep_bytes"]
    return counters, times


def forecasts(times, features, name, program):
    lo, hi = (features[k, program] for k in ("ascending", "shuffled"))
    start, end = (median(times[k, program]) for k in ("ascending", "shuffled"))
    point = features[name, program]

    def fraction(field):
        if hi[field] == lo[field]:
            return Fraction(1, 2)
        value = Fraction(point[field] - lo[field], hi[field] - lo[field])
        return min(Fraction(1), max(Fraction(0), value))

    branch, stream = fraction("bcm"), fraction("disruptive")
    rates = dict(midpoint=Fraction(1, 2), distance=fraction("position_bits"), pages=fraction("misses"),
                 branch=branch, branch_pages=(branch + fraction("misses")) / 2,
                 stream=stream, branch_stream=(branch + stream) / 2)
    for method, field in FILTERS.items():
        adjusted = min(stream, fraction(field)) if hi[field] != lo[field] else stream
        rates[method] = (branch + adjusted) / 2
    estimates = {k: ceil((1 - value) * start + value * end) for k, value in rates.items()}
    estimates["ordinary"] = ceil(Fraction(start * point["work"], lo["work"]))
    estimates["midpoint_adjusted"] = ceil(Fraction(estimates["midpoint"] + estimates["branch_stream"], 2))
    return estimates


class FixedBranchRange(unittest.TestCase):
    def test_reversed_anchors_clamping_equal_counts_and_rounding(self):
        high_ns = 2**55 + 19
        low_ns = high_ns - 9
        start = dict(median_native_ns=high_ns, features={"bcm": 10})
        end = dict(median_native_ns=low_ns, features={"bcm": 5})
        self.assertEqual(fixed_branch_range(start, end, 1), [low_ns, low_ns + 5])
        self.assertEqual(fixed_branch_range(start, end, 20), [low_ns + 5, high_ns])
        end["features"]["bcm"] = 10
        self.assertEqual(fixed_branch_range(start, end, 999), [low_ns + 3, low_ns + 7])
        self.assertEqual(fixed_branch_range(end, start, 0), [low_ns + 3, low_ns + 7])


class ScrivenerRankReplay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        cls.plan_path = EXPERIMENTS / "scrivener_rank/plan.json"
        cls.plan = load(cls.plan_path)
        cls.boots = []
        for suffix in ("", "-repeat"):
            published = EXPERIMENTS / "results" / ("gce-scrivener-rank" + suffix)
            output = unpack(published / "output.tar.gz", Path(tmp.name) / published.name)
            run = load(output / "run.json")
            features, times = raw_samples(output, run["rows"], cls.plan["binaries"])
            cls.boots.append((published, output, run, load(output / "predictions.json"), verify(output), features, times))

    def test_registered_boots_raw_archives_and_pre_label_seals(self):
        freezes, boots = [], []
        anchors = set(self.plan["anchors"])
        for published, output, run, seal, result, _, _ in self.boots:
            self.assertEqual(result, load(published / "metrics.json"))
            receipt = load(published / "collection.json")
            self.assertEqual(receipt["output_sha256"], sha256((published / "output.tar.gz").read_bytes()).hexdigest())
            self.assertEqual(receipt["freeze"]["plan_sha256"], sha256(self.plan_path.read_bytes()).hexdigest())
            platform, cleanup = receipt["platform"], receipt["cleanup"]
            self.assertEqual((platform["cpu_platform"], platform["machine_type"], platform["threads_per_core"]),
                             ("AMD Milan", "n2d-standard-4", 1))
            self.assertEqual((cleanup["instance_http"], cleanup["boot_disk_http"], cleanup["key_removed"]), (404, 404, True))
            self.assertEqual((run["placement"]["cpu"], run["cgroup_allowed_cpus"]), (1, "1"))
            rows = run["rows"]
            cases, solvers = len(self.plan["cases"]), len(self.plan["binaries"])
            counts = dict(scan=cases * 3, ordinary=cases * solvers * 2, branch=cases * solvers * 2,
                          native=cases * solvers * 9)
            self.assertEqual(Counter(r["arm"] for r in rows), counts)
            self.assertEqual((result["verified_pids"], len({r["launcher_pid"] for r in rows})), (len(rows), len(rows)))
            cutoff = result["sealed_prefix_rows"]
            self.assertEqual(cutoff, counts["scan"] + counts["ordinary"] + counts["branch"] + 9 * len(anchors) * solvers)
            self.assertTrue(all(r["arm"] != "native" or r["name"] in anchors for r in rows[:cutoff]))
            self.assertTrue(all(r["arm"] == "native" and r["name"] not in anchors for r in rows[cutoff:]))
            canonical = json.dumps(rows[:cutoff], sort_keys=True, separators=(",", ":")).encode()
            self.assertEqual(seal["prefix_sha256"], sha256(canonical).hexdigest())
            self.assertEqual(run["predictions_sha256"], sha256((output / "predictions.json").read_bytes()).hexdigest())
            self.assertEqual(set(seal["cases"]), set(corpus.FRESH) | set(corpus.CONTROLS))
            self.assertEqual(set(result["controls"]), set(corpus.CONTROLS))
            self.assertEqual(set(result["cases"]), anchors | set(corpus.FRESH))
            freezes.append(receipt["freeze"])
            boots.append(run["placement"]["boot_sha256"])
        self.assertEqual(*freezes)
        self.assertNotEqual(*boots)

    def test_every_sealed_forecast_and_native_vote_from_raw_files(self):
        for _, _, _, seal, report, features, times in self.boots:
            for name in self.plan["anchors"]:
                for row in report["cases"][name].values():
                    self.assertNotIn("fixed_branch_range_ns", row)
            for name, programs in seal["cases"].items():
                for program, observed in programs.items():
                    expect = forecasts(times, features, name, program)
                    self.assertEqual(observed, expect, (name, program))
                    group = "controls" if name in corpus.CONTROLS else "cases"
                    row = report[group][name][program]
                    self.assertEqual(row["predictions_ns"], expect)
                    low, high = (median(times[k, program]) for k in ("ascending", "shuffled"))
                    lo_b, hi_b = (features[k, program]["bcm"] for k in ("ascending", "shuffled"))
                    observed_b = features[name, program]["bcm"]
                    branch = (min(Fraction(1), max(Fraction(0), Fraction(observed_b - lo_b, hi_b - lo_b)))
                              if hi_b != lo_b else Fraction(1, 2))
                    endpoints = sorted(ceil((2 - branch - scan) * low / 2 + (branch + scan) * high / 2)
                                       for scan in (0, 1))
                    self.assertEqual(row["fixed_branch_range_ns"], endpoints)
                    self.assertEqual(row["features"], features[name, program])
                    native = times[name, program]
                    self.assertEqual(len(native), 9)
                    self.assertEqual(row["native_ns"], native)
                    self.assertEqual(row["native_tle_votes"],
                                     {str(ms): sum(n > ms * MS for n in native) for ms in self.plan["fixed_limits_ms"]})

    def test_heldout_error_gates_and_individual_deadline_changes(self):
        limits = self.plan["fixed_limits_ms"]
        for _, _, _, _, report, features, times in self.boots:
            errors = defaultdict(list)
            grid = defaultdict(Counter)
            wrong = defaultdict(lambda: {"false_timeout": set(), "false_pass": set()})
            for name in corpus.FRESH:
                for program in self.plan["binaries"]:
                    native = times[name, program]
                    for method, ns in forecasts(times, features, name, program).items():
                        errors[method].append(abs(Fraction(ns, median(native)) - 1))
                        grid[method].update(classifications(limits, ns, native))
                        for ms in limits:
                            if max(native) <= ms * MS < ns:
                                wrong[method]["false_timeout"].add((name, program, ms))
                            elif ns <= ms * MS < min(native):
                                wrong[method]["false_pass"].add((name, program, ms))
            for method, values in errors.items():
                summary = dict(report["summary"][method])
                self.assertEqual(summary.pop("cases"), len(values))
                self.assertEqual(summary.pop("fixed_limits"), len(values) * len(limits))
                self.assertEqual(summary.pop("within_20pct"), sum(v <= Fraction(1, 5) for v in values))
                self.assertEqual(summary.pop("worst_absolute_error_ppm"), round(max(values) * MS))
                self.assertEqual(summary.pop("median_absolute_error_ppm"), round(median(values) * MS))
                summary["mixed"] = summary.pop("mixed_limits")
                self.assertEqual(Counter(summary), grid[method])
            baseline = "branch_stream"
            candidates = ("midpoint_adjusted", *FILTERS)
            self.assertEqual(set(report["qualification"]), set(candidates))
            for method in candidates:
                gate = report["qualification"][method]
                worst = max(errors[method]) < max(errors[baseline])
                typical = median(errors[method]) <= median(errors[baseline])
                self.assertEqual((gate["worst_error_improved"], gate["median_error_nonincreasing"],
                                  gate["accuracy_gate"]), (worst, typical, worst and typical))
                fixes, additions = 0, 0
                for label, observed in wrong[method].items():
                    before = wrong[baseline][label]
                    added, fixed = observed - before, before - observed
                    changes = gate["unanimous_error_changes"]
                    self.assertCountEqual(map(tuple, changes[label + "_added"]), added)
                    self.assertCountEqual(map(tuple, changes[label + "_fixed"]), fixed)
                    fixes += len(fixed)
                    additions += len(added)
                self.assertEqual(gate["deadline_gate"], fixes > 0 and additions == 0)

    def test_rejects_changed_rank_count_and_unregistered_scanner_launch(self):
        _, root, run, _, _, _, _ = self.boots[0]
        row = next(r for r in run["rows"] if r["arm"] == "scan")
        scan = root / "runs" / f"{row['sequence']:03d}" / "scan.out"
        original = scan.read_bytes()
        try:
            fields = list(map(int, original.split()))
            fields[-1] += 1
            scan.write_text(" ".join(map(str, fields)) + "\n")
            with self.assertRaisesRegex(ValueError, "mismatched raw receipt"):
                verify(root)
        finally:
            scan.write_bytes(original)

        trace = root / "exec-trace"
        original = trace.read_bytes()
        try:
            extra = b"\nscan [001] .... 9.000: sched_process_exec: filename=/payload/binaries/scan pid=987654321 old_pid=987654321\n"
            trace.write_bytes(original + extra)
            with self.assertRaisesRegex(ValueError, "unregistered measured process"):
                verify(root)
        finally:
            trace.write_bytes(original)
