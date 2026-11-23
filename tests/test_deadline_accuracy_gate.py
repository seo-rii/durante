from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments"))
from deadline_accuracy import gate


class GateRange(unittest.TestCase):
    def test_inclusive_integer_band(self):
        for ns, close in ((74_999_999, False), (75_000_000, True), (100_000_000, True),
                          (125_000_000, True), (125_000_001, False)):
            self.assertEqual(gate.near(ns, 100), close)
        for ns, ms in ((True, 1), (0, False), (0, 0), (-1, 12), (2.0, 1)):
            with self.assertRaises(ValueError):
                gate.near(ns, ms)


class MedianPacket(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temp.cleanup)
        cls.archive = Path(temp.name)
        prefix = "output/cases/fenwick/"
        with tarfile.open(ROOT / "experiments/results/gce-footprint-admission/output.tar.xz") as source:
            for member in source:
                if not member.isfile() or not member.name.startswith(prefix):
                    continue
                name = Path(member.name[len(prefix):])
                if name.parts[0] in ("expanded", "expanded_repeat") and name.parts[1] != "limits":
                    if name.is_absolute() or ".." in name.parts:
                        raise ValueError("unexpected fixture archive path")
                    dest = cls.archive / name
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(source.extractfile(member).read())

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.elf = self.tmp / "program"
        self.packet = self.tmp / "packet"
        self.origin = gate.read_json((self.archive / "expanded/policy.json").read_bytes())
        self.host = dict(kernel=self.origin["native"]["kernel"], machine=self.origin["native"]["machine"],
                         cpu=1, boot_sha256="1"*64)
        self.source = self.tmp / "policy.json"
        self.selection = self.tmp / "source-selection.json"

    def select(self, case, boot=True):
        self.capture = self.archive / case
        self.stdin = self.capture / "full/stdin"
        if boot:
            self.origin["native"]["boot_sha256"] = self.host["boot_sha256"]
        self.origin["calibration_sha256"] = gate.anchor.calibration_digest(self.origin["anchor"], self.origin["native"])
        self.source.write_text(json.dumps(self.origin))
        selected = gate.read_json((self.capture / "footprint-selection.json").read_bytes())
        selected["policy_sha256"] = gate.elf_digest(self.source)
        self.selection.write_text(json.dumps(selected))
        self.ms = round(selected["prediction_ns"] / 1_000_000)

    def mock_elf_and_host(self):
        digest = self.origin["anchor"]["elf_sha256"]
        for stub in (patch.object(gate.anchor, "static_elf", return_value=digest),
                     patch.object(gate.anchor, "elf_digest",
                                  side_effect=lambda p: digest if p == self.elf else gate.elf_digest(p)),
                     patch.object(gate, "placement", side_effect=lambda: self.host.copy())):
            stub.start()
            self.addCleanup(stub.stop)

    def sample(self, pid, us):
        launch, = gate.read_json((self.capture / "full/result.json").read_bytes())["launches"]
        return dict(self.origin["native"], boot_sha256=self.host["boot_sha256"], pid=pid,
                    cpu_ns=1000*us, user_us=us, sys_us=0, stdout_sha256=launch["stdout_sha256"])

    def prepare(self, ms=None, strategy="near-three"):
        return gate.prepare(self.elf, self.stdin, self.capture, self.selection, self.source,
                            self.packet, self.ms if ms is None else ms, strategy)

    def test_exact_one_transferred_policy_pays_once_across_far_and_near_limits(self):
        self.select("expanded_repeat")
        self.mock_elf_and_host()
        with patch.object(gate.anchor, "native_once", return_value=self.sample(521, 111_000)) as run:
            with ThreadPoolExecutor(max_workers=2) as pool:
                far, close = list(pool.map(lambda ms: self.prepare(ms, "exact-one"), (1, self.ms)))
        self.assertEqual(run.call_count, 1)
        self.assertFalse(far["near"])
        self.assertTrue(close["near"])
        self.assertEqual(dict(far, near=True), close)
        self.assertEqual({p.name for p in self.packet.iterdir()}, {"policy-0.json", "packet.json", "selection.json"})
        manifest = gate.read_json(far["packet"].read_bytes())
        self.assertEqual((manifest["schema"], manifest["strategy"], manifest["reused_original"],
                          manifest["selected_index"]), (2, "exact-one", False, 0))
        self.assertEqual(far["prediction_ns"], 111_000_000)
        self.assertEqual(manifest["policy_sha256"], [gate.elf_digest(far["policy"])])
        selected = gate.read_json(far["selection"].read_bytes())
        self.assertEqual((selected["action"], selected["comparisons"], selected["reference_index"]),
                         ("calibrate", [], None))

    def test_exact_one_current_capture_reuses_paid_observation_but_rechecks_packet(self):
        self.select("expanded")
        self.mock_elf_and_host()
        with patch.object(gate.anchor, "native_once", side_effect=AssertionError("unexpected native sampling")):
            far = self.prepare(1, "exact-one")
            self.assertFalse(far["near"])
            self.assertEqual(far["policy"].read_bytes(), self.source.read_bytes())
            manifest = gate.read_json(far["packet"].read_bytes())
            self.assertTrue(manifest["reused_original"])
            with self.assertRaisesRegex(ValueError, "calibration strategy"):
                self.prepare()
            self.host["cpu"] += 1
            with self.assertRaisesRegex(ValueError, "pinned context"):
                self.prepare(1, "exact-one")
            self.host["cpu"] -= 1
            manifest["selected_index"] = False
            far["packet"].write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "stored packet"):
                self.prepare(1, "exact-one")

    def test_exact_one_remeasures_current_capture_when_boot_changed_and_discards_failure(self):
        self.select("expanded", boot=False)
        self.mock_elf_and_host()
        samples = [dict(self.sample(511, 100_000), stdout_sha256="0"*64), self.sample(512, 200_000)]
        with patch.object(gate.anchor, "native_once", side_effect=samples) as run:
            with self.assertRaisesRegex(ValueError, "calibration output"):
                self.prepare(1, "exact-one")
            self.assertFalse(self.packet.exists())
            choice = self.prepare(1, "exact-one")
        self.assertEqual(run.call_count, 2)
        self.assertFalse(gate.read_json(choice["packet"].read_bytes())["reused_original"])
        self.assertEqual(choice["prediction_ns"], 200_000_000)

    def test_far_uses_existing_policy_without_creating_packet(self):
        self.select("expanded_repeat")
        self.mock_elf_and_host()
        with patch.object(gate.anchor, "native_once", side_effect=AssertionError("far limit paid")):
            result = self.prepare(1)
        self.assertFalse(result["near"])
        self.assertEqual((result["selection"], result["policy"], result["packet"]), (self.selection, self.source, None))
        self.assertFalse(self.packet.exists())

    def test_original_exact_capture_is_one_vote_and_can_be_the_median(self):
        self.select("expanded")
        self.mock_elf_and_host()
        samples = [self.sample(500, 1_000), self.sample(501, 500_000)]
        with patch.object(gate.anchor, "native_once", side_effect=samples) as run:
            choice = self.prepare()
        manifest = gate.read_json(choice["packet"].read_bytes())
        self.assertTrue(manifest["reused_original"])
        self.assertEqual(run.call_count, 2)
        self.assertEqual(choice["policy"].read_bytes(), self.source.read_bytes())
        self.assertEqual(choice["prediction_ns"], self.origin["native"]["cpu_ns"])

    def test_transferred_policy_runs_three_independent_jobs_once_under_concurrency(self):
        self.select("expanded_repeat")
        self.mock_elf_and_host()
        samples = [self.sample(711, 150_000), self.sample(712, 80_000), self.sample(713, 99_000)]
        with patch.object(gate.anchor, "native_once", side_effect=samples) as run:
            with ThreadPoolExecutor(max_workers=2) as pool:
                first, second = list(pool.map(self.prepare, (self.ms, self.ms+1)))
        self.assertEqual(first, second)
        self.assertEqual(run.call_count, 3)
        manifest = gate.read_json(first["packet"].read_bytes())
        self.assertFalse(manifest["reused_original"])
        self.assertEqual(manifest["median_index"], 2)
        self.assertEqual(first["prediction_ns"], 99_000_000)
        self.assertEqual(gate.read_json(first["policy"].read_bytes())["native"]["pid"], 713)
        selection = gate.read_json(first["selection"].read_bytes())
        self.assertEqual((selection["comparisons"], selection["action"]), ([], "calibrate"))

    def test_changed_boot_or_rewritten_packet_rejected_without_new_timings(self):
        self.select("expanded", boot=False)
        self.mock_elf_and_host()
        samples = [self.sample(720, 90_000), self.sample(721, 90_000), self.sample(722, 90_000)]
        with patch.object(gate.anchor, "native_once", side_effect=samples) as run:
            choice = self.prepare()
        self.assertEqual(run.call_count, 3)
        manifest = gate.read_json(choice["packet"].read_bytes())
        self.assertFalse(manifest["reused_original"])
        self.assertEqual(manifest["median_index"], 1)
        with patch.object(gate.anchor, "native_once", side_effect=AssertionError("rewrote stale packet")):
            self.host["boot_sha256"] = "2"*64
            with self.assertRaisesRegex(ValueError, "pinned context"):
                self.prepare()
            self.host["boot_sha256"] = "1"*64
            manifest["median_index"] = True
            choice["packet"].write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "stored packet"):
                self.prepare()

    def test_duplicate_pid_or_incomplete_capture_never_published(self):
        self.select("expanded_repeat")
        self.mock_elf_and_host()
        repeated = self.sample(730, 1_000)
        with patch.object(gate.anchor, "native_once", return_value=repeated):
            with self.assertRaisesRegex(ValueError, "PID"):
                self.prepare()
        self.assertFalse(self.packet.exists())
        with patch.object(gate.anchor, "native_once", side_effect=[repeated, OSError("interrupted")]):
            with self.assertRaisesRegex(OSError, "interrupted"):
                self.prepare()
        self.assertFalse(self.packet.exists())
        with patch.object(gate.anchor, "native_once", side_effect=[self.sample(740+i, 2_000+i) for i in range(3)]):
            self.assertTrue(self.prepare()["near"])

    def test_native_output_mismatch_aborts_before_publication(self):
        self.select("expanded_repeat")
        self.mock_elf_and_host()
        bad = dict(self.sample(750, 1_000), stdout_sha256="0"*64)
        with patch.object(gate.anchor, "native_once", return_value=bad) as run:
            with self.assertRaisesRegex(ValueError, "calibration output"):
                self.prepare()
        self.assertEqual(run.call_count, 1)
        self.assertFalse(self.packet.exists())

    def test_wrong_supplied_input_or_selection_never_reaches_native_sampler(self):
        self.select("expanded_repeat")
        self.mock_elf_and_host()
        with patch.object(gate.anchor, "native_once", side_effect=AssertionError("unexpected native sample")):
            self.stdin = self.archive / "expanded/full/stdin"
            with self.assertRaisesRegex(ValueError, "requested input"):
                self.prepare()
            self.stdin = self.capture / "full/stdin"
            wrong = gate.read_json(self.selection.read_bytes())
            wrong["prediction_ns"] += 1
            self.selection.write_text(json.dumps(wrong))
            with self.assertRaisesRegex(ValueError, "selection differs"):
                self.prepare()
        self.assertFalse(self.packet.exists())

    def run_cli(self, out, ms, *args):
        cpu = min(os.sched_getaffinity(0))
        cmd = ["taskset", "-c", str(cpu), sys.executable, "-B", gate.__file__,
               "--payload", os.environ["DURANTE_SHAPE_RUNTIME"], "--elf",
               Path(os.environ["DURANTE_SHAPE_BINARIES"]) / "fenwick", "--capture", self.capture,
               "--selection", self.selection, "--policy", self.source, "--packet-dir", self.packet,
               "--out", out, "--stdin", self.stdin, "--limit-ms", str(ms), *args]
        proc = subprocess.run(cmd, capture_output=True, timeout=60)
        self.assertIn(proc.returncode, (0, 123), (proc.stdout, proc.stderr))
        result = gate.read_json(proc.stdout)
        self.assertEqual(result["status"] == "budget_exceeded", result["prediction_ns"] > ms * 1_000_000)
        return result

    @unittest.skipUnless(os.getenv("DURANTE_SHAPE_RUNTIME") and os.getenv("DURANTE_SHAPE_BINARIES"),
                         "requires registered v11 runtime and workload ELFs")
    def test_public_cli_creates_three_native_votes_and_enforces_a_new_limit(self):
        self.select("expanded_repeat", boot=False)
        out = self.tmp / "live"
        result = self.run_cli(out, self.ms)
        self.assertTrue(gate.read_json((out / "gate.json").read_bytes())["near"])
        natives = [gate.read_json((self.packet / f"policy-{i}.json").read_bytes())["native"] for i in range(3)]
        self.assertEqual(len({v["pid"] for v in natives} | {result["pid"]}), 4)
        self.assertEqual(result["preflight_prediction_ns"], sorted(v["cpu_ns"] for v in natives)[1])

    @unittest.skipUnless(os.getenv("DURANTE_SHAPE_RUNTIME") and os.getenv("DURANTE_SHAPE_BINARIES"),
                         "requires registered v11 runtime and workload ELFs")
    def test_exact_one_cli_binds_far_and_near_to_the_same_real_observation(self):
        self.select("expanded_repeat", boot=False)
        enforcements = set()
        packet_sha = None
        for ms, nearby in ((1, False), (self.ms, True)):
            out = self.tmp / f"limit-{ms}"
            result = self.run_cli(out, ms, "--strategy", "exact-one")
            enforcements.add(result["pid"])
            receipt = gate.read_json((out / "gate.json").read_bytes())
            self.assertEqual((receipt["near"], receipt["strategy"]), (nearby, "exact-one"))
            digest = gate.elf_digest(self.packet / "packet.json")
            self.assertEqual(receipt["packet_sha256"], digest)
            if packet_sha is not None:
                self.assertEqual(digest, packet_sha)
            packet_sha = digest
            native = gate.read_json((self.packet / "policy-0.json").read_bytes())["native"]
            self.assertEqual(result["preflight_prediction_ns"], native["cpu_ns"])
            self.assertNotIn(native["pid"], enforcements)
        self.assertEqual(len(enforcements), 2)
