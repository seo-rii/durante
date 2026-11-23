"""Check both registered interleaved collections against raw wait4 data and fresh live verdicts."""
from collections import Counter, defaultdict
from contextlib import contextmanager
from fractions import Fraction
import json
from pathlib import Path
from statistics import median
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments"))
from contest_batch.verify import verify
from shape_calibration.verify_combined import unpack

STUDIES = ROOT / "experiments/results"
PLAN = ROOT / "experiments/contest_batch/plan.json"


def read(path):
    return json.loads(path.read_bytes())


@contextmanager
def replace(path, new):
    saved = path.read_bytes()
    path.write_bytes(new)
    try:
        yield
    finally:
        path.write_bytes(saved)


def cpu_ns(row):
    return 1000 * (row["user_us"] + row["sys_us"])


class BatchReplay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        cls.archives, cls.outputs = [], []
        for suffix in ("", "-repeat"):
            study = STUDIES / ("gce-contest-batch" + suffix)
            work = Path(tmp.name) / ("guest" + suffix)
            work.mkdir()
            cls.archives.append(study)
            cls.outputs.append(unpack(study / "output.tar.xz", work))
        cls.plan = read(PLAN)

    def test_duration_and_verdicts_from_independent_observations_on_two_boots(self):
        guests = []
        for root, archive in zip(self.outputs, self.archives, strict=True):
            summary = read(root / "summary.json")
            result = verify(root)
            self.assertEqual(result, read(archive / "metrics.json"))
            natives = defaultdict(list)
            for row in summary["native"]:
                natives[row["case_id"]].append(cpu_ns(row))
            self.assertEqual(len(summary["native"]), 11 * len(self.plan["cases"]))
            boots, errors, counts, fixed_votes = set(), defaultdict(list), defaultdict(Counter), {}
            for case in self.plan["cases"]:
                name = case["case_id"]
                folder = root / "batch" / name
                raw = [read(folder / f"policy-{i}.json") for i in range(4)]
                for policy in raw:
                    boots.add(policy["native"]["boot_sha256"])
                    self.assertEqual((policy["anchor"]["elf_sha256"], policy["anchor"]["stdin_sha256"],
                                      policy["native"]["stdout_sha256"]),
                                     (case["binary_sha256"], case["stdin_sha256"], case["stdout_sha256"]))
                first, *extra = [cpu_ns(policy["native"]) for policy in raw]
                calibration = {"baseline": first, "warm": median(extra)}
                samples = natives[name]
                self.assertEqual(len(samples), 11)
                center = median(samples)
                trial = result["cases"][name]
                self.assertEqual((trial["baseline_ns"], trial["extra_ns"], trial["packet_warm_ns"]), (first, extra, median(extra)))
                self.assertEqual((trial["native_ns"], trial["median_ns"]), (samples, center))
                for arm, ns in calibration.items():
                    ppm = round(Fraction(ns - center, center) * 1_000_000)
                    key = "packet_warm" if arm == "warm" else "baseline"
                    self.assertEqual(trial[key + "_signed_error_ppm"], ppm)
                    if case["cohort"] == "prospective":
                        errors[key].append(abs(ppm))
                fixed = set(case["fixed_limits_ms"])
                pct = self.plan["deadline_percent"]
                adaptive = {max(1, (first * v + 99_999_999) // 100_000_000) for v in pct}
                self.assertEqual([p["limit_ms"] for p in trial["deadlines"]], sorted(fixed | adaptive))
                for i, point in enumerate(trial["deadlines"]):
                    ms = point["limit_ms"]
                    votes = sum(t > ms * 1_000_000 for t in samples)
                    self.assertEqual((point["native_tle_reps"], point["native_median_margin_ns"]), (votes, center - ms * 1_000_000))
                    if ms in fixed:
                        fixed_votes[name, ms] = votes
                    for arm in calibration:
                        receipt = read(root / "cases" / name / "limits" / arm / f"{i:02d}/live/result.json")
                        self.assertIn(receipt["status"], ("within_budget", "budget_exceeded"))
                        tle = receipt["status"] == "budget_exceeded"
                        self.assertEqual((receipt["pid"], tle), (point[arm]["pid"], point[arm]["tle"]))
                        if case["cohort"] == "prospective":
                            groups = ["all"] + (["fixed"] if ms in fixed else []) + (["adaptive"] if ms in adaptive else [])
                            for group in groups:
                                tally = counts[arm, group]
                                tally["majority_matches"] += tle == (votes >= 6)
                                if 0 < votes < 11:
                                    tally["mixed_native"] += 1
                                elif tle == (votes == 11):
                                    tally["agreement"] += 1
                                else:
                                    tally["false_tle" if tle else "false_accept"] += 1
            self.assertEqual(len(boots), 1)
            guests.append((boots.pop(), fixed_votes))
            scored = result["totals"]["prospective"]
            self.assertEqual(scored["inputs"], 10)
            for method, absolute in errors.items():
                self.assertEqual(scored[method + "_estimates"]["median_absolute_error_ppm"], median(absolute))
            for (arm, group), tally in counts.items():
                expected = scored["deadlines"][group][arm]
                for metric in ("agreement", "false_tle", "false_accept", "mixed_native", "majority_matches"):
                    self.assertEqual(expected[metric], tally[metric])
        (first_boot, first_fixed), (second_boot, second_fixed) = guests
        self.assertNotEqual(first_boot, second_boot)
        self.assertEqual(first_fixed.keys(), second_fixed.keys())

    def test_manifest_binds_registered_round_order_and_packet_hash(self):
        for root in self.outputs:
            path = root / "batch/manifest.json"
            for field in ("case", "round"):
                changed = read(path)
                if field == "case":
                    changed["cases"][0]["packet_sha256"] = "0" * 64
                else:
                    a = changed["rounds"][1]
                    a[0], a[1] = a[1], a[0]
                with self.subTest(guest=root.parent.name, field=field), replace(path, json.dumps(changed).encode()):
                    with self.assertRaisesRegex(ValueError, "batch manifest differs"):
                        verify(root)

    def test_physical_trace_must_put_calibration_before_independent_reference(self):
        for root in self.outputs:
            directory = root / "batch"
            last = read(directory / "manifest.json")["rounds"][-1][-1]
            paid = read(directory / last / "policy-3.json")["native"]["pid"]
            reference = read(root / "summary.json")["native"][0]["pid"]
            trace = root / "exec-trace"
            lines = trace.read_bytes().splitlines(keepends=True)
            def line_for(pid):
                i, = [j for j, line in enumerate(lines) if f" pid={pid} ".encode() in line]
                return i
            a, b = line_for(paid), line_for(reference)
            self.assertLess(a, b)
            lines[a], lines[b] = lines[b], lines[a]
            with self.subTest(guest=root.parent.name), replace(trace, b"".join(lines)):
                with self.assertRaisesRegex(ValueError, "exec trace order differs"):
                    verify(root)
