"""Check published Scrivener forecasts and paired CPU costs directly from the raw guest journals."""
from collections import defaultdict
from fractions import Fraction
from hashlib import sha256
import json
from math import ceil
from pathlib import Path
from statistics import median
import sys
import tarfile
import tempfile
import unittest

EXPERIMENTS = Path(__file__).resolve().parents[1] / "experiments"
sys.path.insert(0, str(EXPERIMENTS))
from scrivener_order import corpus
from scrivener_order.verify import verify

RESULTS = EXPERIMENTS / "results"
MS = 1_000_000


def load(path):
    return json.loads(path.read_bytes())


def unpack(path, dest):
    with tarfile.open(path, "r:gz") as archive:
        for member in archive:
            name = Path(member.name)
            if not name.parts or name.parts[0] != "output" or ".." in name.parts:
                raise ValueError("unexpected archive path")
            if member.isdir():
                continue
            if not member.isfile():
                raise ValueError("unexpected archive entry type")
            target = dest / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as stream:
                stream.write(archive.extractfile(member).read())
    return dest / "output"


def counts(root, row):
    record = root / "runs" / f"{row['sequence']:03d}" / "records" / f"process-{row['launcher_pid']}.jsonl"
    raw = record.read_bytes()
    terminal = json.loads(raw.splitlines()[-1])
    if terminal["event"] != "end" or sha256(raw).hexdigest() != row["record_sha256"]:
        raise ValueError("invalid terminal record or digest")
    result = {key: terminal[key] for key in ("instructions", "rep_bytes")}
    if "latency_bound_x16" in terminal:
        result.update(cycles_x16=terminal["latency_bound_x16"], memory_operand_bytes=terminal["memory_operand_bytes"])
    return result


def successive_position_bits(name):
    text, _ = corpus.make(name)
    positions = [int(line[2:]) for line in text.splitlines() if line.startswith("P ")]
    return sum(abs(current - previous).bit_length() for previous, current in zip(positions, positions[1:]))


class ScrivenerOrderReplay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        cls.boots = []
        for index, suffix in enumerate(("", "-repeat")):
            result = RESULTS / ("gce-scrivener-order" + suffix)
            root = unpack(result / "output.tar.gz", Path(tmp.name) / str(index))
            cls.boots.append((root, result, verify(root)))
        # Both archived #95 boots captured these same production counter values.
        cls.historical = []
        for index, suffix in enumerate(("", "-repeat")):
            previous = RESULTS / ("gce-contest-submissions" + suffix) / "output.tar.gz"
            root = unpack(previous, Path(tmp.name) / f"prior-{index}")
            journal = load(root / "run.json")
            cls.historical.append({r["program"]: counts(root, r) for r in journal["rows"]
                                   if r["case_id"] == "scrivener/interwoven_pages" and r["kind"] == "profile"})

    def test_collection_and_registered_records(self):
        boot_ids, payloads = set(), set()
        for root, result, report in self.boots:
            self.assertEqual(report, load(result / "metrics.json"))
            receipt = load(result / "collection.json")
            self.assertEqual(sha256((result / "output.tar.gz").read_bytes()).hexdigest(), receipt["output_sha256"])
            cleanup = receipt["cleanup"]
            self.assertEqual((cleanup["instance_http"], cleanup["boot_disk_http"], cleanup["key_removed"]),
                             (404, 404, True))
            platform = receipt["platform"]
            self.assertEqual((platform["cpu_platform"], platform["threads_per_core"], platform["machine_type"]),
                             ("AMD Milan", 1, "n2d-standard-4"))
            run = load(root / "run.json")
            self.assertEqual(report["boot_sha256"], run["placement"]["boot_sha256"])
            self.assertEqual(report["plan_sha256"], sha256((EXPERIMENTS / "scrivener_order/plan.json").read_bytes()).hexdigest())
            self.assertEqual(report["cpu_steal_ticks"], run["cpu_steal_ticks"])
            self.assertEqual(report["verified_pids"], 112)
            self.assertEqual(len({r["launcher_pid"] for r in run["rows"]}), 112)
            boot_ids.add(report["boot_sha256"])
            freeze = receipt["freeze"]
            self.assertEqual(freeze["plan_sha256"], report["plan_sha256"])
            self.assertRegex(freeze["payload_sha256"], "^[0-9a-f]{64}$")
            payloads.add(freeze["payload_sha256"])
        self.assertEqual(len(boot_ids), 2)
        self.assertEqual(len(payloads), 1)

    def test_paired_cpu_and_terminal_counters(self):
        self.assertEqual(self.historical[0], self.historical[1])
        for root, _, report in self.boots:
            rows = defaultdict(lambda: defaultdict(list))
            for row in load(root / "run.json")["rows"]:
                rows[(row["name"], row["program"])][row["arm"]].append(row)
            for (name, program), arms in rows.items():
                with self.subTest(name=name, program=program):
                    score = report["cases"][name][program]
                    cpu = {arm: [r["user_us"] + r["sys_us"] for r in runs]
                           for arm, runs in arms.items()}
                    self.assertEqual(cpu["previous"], score["previous_us"])
                    self.assertEqual(cpu["current"], score["current_us"])
                    self.assertEqual(cpu["diagnostic"], [score["diagnostic_us"]])
                    pairs = [Fraction(a, b) for a, b in zip(cpu["previous"], cpu["current"], strict=True)]
                    self.assertEqual(len(pairs), 2)
                    self.assertEqual(score["previous_to_current_ratio_ppm"], round(MS * median(pairs)))
                    labels = [us * 1000 for us in cpu["native"]]
                    self.assertEqual(len(labels), 9)
                    self.assertEqual(labels, score["native_ns"])
                    self.assertEqual(median(labels), score["native_median_ns"])
                    full = counts(root, arms["diagnostic"][0])
                    self.assertEqual(full, score["counters"])
                    expected = {key: full[key] for key in ("instructions", "rep_bytes")}
                    for arm in ("previous", "current"):
                        self.assertEqual(score[arm + "_counts"], [counts(root, r) for r in arms[arm]])
                    identical = all(c == expected for arm in ("previous", "current") for c in score[arm + "_counts"])
                    self.assertEqual(score["cheap_counters_match"], identical)
                    if name == "interwoven_pages":
                        self.assertEqual(report["archived_control_matches"][program], self.historical[0][program] == full)

    def test_held_out_forecasts_and_votes(self):
        x = {name: successive_position_bits(name) for name in ("ascending", "tiled", "shuffled")}
        self.assertLess(x["ascending"], x["tiled"])
        self.assertLess(x["tiled"], x["shuffled"])
        fraction = Fraction(x["tiled"] - x["ascending"], x["shuffled"] - x["ascending"])
        source = load(EXPERIMENTS / "guarded_native/archived_reference.json")
        weights = {key: Fraction(value) for key, value in source["combined_reference_weights"].items()}
        for root, _, report in self.boots:
            raw = load(root / "run.json")["rows"]
            for program, forecast in report["order_forecast"].items():
                by_case = defaultdict(list)
                for row in raw:
                    if row["program"] == program and row["arm"] == "native":
                        by_case[row["name"]].append((row["user_us"] + row["sys_us"]) * 1000)
                actual = median(by_case["tiled"])
                low, high = median(by_case["ascending"]), median(by_case["shuffled"])
                work = {}
                for name in ("ascending", "tiled"):
                    profile = next(r for r in raw if r["program"] == program and r["name"] == name and r["arm"] == "current")
                    measures = counts(root, profile)
                    work[name] = sum(w * measures[k] for k, w in weights.items())
                baseline = ceil(low * work["tiled"] / work["ascending"])
                midpoint = ceil(Fraction(low + high, 2))
                candidate = ceil(low + fraction * (high - low))
                self.assertEqual((forecast["ascending_only_ns"], forecast["interpolated_ns"], forecast["actual_ns"]),
                                 (baseline, candidate, actual))
                self.assertEqual(forecast["midpoint_ns"], midpoint)
                self.assertEqual(forecast["midpoint_error_ppm"], round(MS * Fraction(midpoint - actual, actual)))
                self.assertEqual(forecast["ascending_error_ppm"], round(MS * Fraction(baseline - actual, actual)))
                self.assertEqual(forecast["interpolated_error_ppm"], round(MS * Fraction(candidate - actual, actual)))
                limits = [25, 50, 75, 100, 150, 175, 200]
                self.assertEqual(set(forecast["limits"]), {str(ms) for ms in limits})
                matches = {"ascending": 0, "interpolated": 0, "midpoint": 0}
                for ms in limits:
                    deadline = ms * MS
                    votes = sum(ns > deadline for ns in by_case["tiled"])
                    self.assertEqual(forecast["native_tle_votes"][str(ms)], votes)
                    self.assertEqual(forecast["limits"][str(ms)], dict(ascending_tle=baseline > deadline,
                                                                      interpolated_tle=candidate > deadline,
                                                                      midpoint_tle=midpoint > deadline))
                    for name, value in (("ascending", baseline), ("interpolated", candidate), ("midpoint", midpoint)):
                        matches[name] += (value > deadline) == (votes >= 5)
                self.assertEqual(forecast["majority_correct"], matches)

    def test_rejects_changed_profile_digest_and_cpu_placement(self):
        root = self.boots[0][0]
        journal = root / "run.json"
        original = journal.read_bytes()
        try:
            run = json.loads(original)
            old_sha = run["rows"][0]["record_sha256"]
            run["rows"][0]["record_sha256"] = "0" * 64
            self.assertNotEqual(old_sha, run["rows"][0]["record_sha256"])
            journal.write_text(json.dumps(run))
            with self.assertRaisesRegex(ValueError, "profile digest"):
                verify(root)
            run["rows"][0]["record_sha256"] = old_sha
            run["cgroup_allowed_cpus"] = "0-1"
            journal.write_text(json.dumps(run))
            with self.assertRaisesRegex(ValueError, "guest placement"):
                verify(root)
        finally:
            journal.write_bytes(original)
