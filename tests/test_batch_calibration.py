"""Check round scheduling and immutable batch publication with authentic complete capture receipts."""
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
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
from contest_batch import batch


class BatchCalibration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temp.cleanup)
        cls.root = Path(temp.name)
        cls.names = ["money/dense_scrambled", "money/mostly_dominated", "money/narrow_windows"]
        with tarfile.open(ROOT / "experiments/results/gce-contest-deadlines/output.tar.xz") as archive:
            for member in archive:
                if not member.isfile() or not member.name.startswith("output/cases/"):
                    continue
                relative = Path(member.name.removeprefix("output/cases/"))
                if len(relative.parts) < 3 or "/".join(relative.parts[:2]) not in cls.names:
                    continue
                if relative.parts[2] in ("limits", "deadline-packet"):
                    continue
                if ".." in relative.parts or relative.is_absolute():
                    raise ValueError("unexpected capture archive path")
                target = cls.root / relative
                target.parent.mkdir(exist_ok=True, parents=True)
                target.write_bytes(archive.extractfile(member).read())

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.out = Path(temp.name) / "output"
        sources = [json.loads((self.root / name / "policy.json").read_bytes()) for name in self.names]
        n = sources[0]["native"]
        self.host = dict(cpu=1, kernel=n["kernel"], machine=n["machine"], boot_sha256=n["boot_sha256"])
        self.elf = self.root / "money-elf"
        elf_sha = sources[0]["anchor"]["elf_sha256"]
        self.items = [dict(id=name, elf=self.elf, stdin=self.root / name / "full/stdin", capture=self.root / name)
                      for name in self.names]
        self.native_by_input = {(self.root / name / "full/stdin").read_bytes(): (name, s["native"])
                                for name, s in zip(self.names, sources, strict=True)}
        for stub in (patch.object(batch.gate, "placement", side_effect=lambda: self.host.copy()),
                     patch.object(batch.anchor, "static_elf", return_value=elf_sha),
                     patch.object(batch.anchor, "elf_digest",
                                  side_effect=lambda p: elf_sha if p == self.elf else batch.elf_digest(p))):
            stub.start()
            self.addCleanup(stub.stop)
        self.launched = []

    def sample(self, elf, stdin):
        self.assertEqual(elf, self.elf)
        name, original = self.native_by_input[stdin]
        self.launched.append(name)
        seq = len(self.launched)
        return dict(original, pid=75000 + seq, sys_us=0, user_us=seq * 1000, cpu_ns=seq * 1_000_000)

    def test_rotating_rounds_are_measured_once_and_replay_with_the_normal_operator(self):
        with patch.object(batch.anchor, "native_once", side_effect=self.sample):
            with ThreadPoolExecutor(max_workers=2) as pool:
                one, two = list(pool.map(lambda _: batch.prepare(self.items, self.out), range(2)))
        self.assertEqual(one, two)
        keys = sorted(self.names, key=lambda s: sha256(s.encode()).digest())
        schedule = [keys, keys[1:] + keys[:1], keys[2:] + keys[:2]]
        self.assertEqual(one["rounds"], schedule)
        self.assertEqual(one["placement"], self.host)
        self.assertEqual([c["id"] for c in one["cases"]], self.names)
        self.assertEqual(self.launched, sum(schedule, []))
        with patch.object(batch.anchor, "native_once", side_effect=AssertionError("unexpected second payment")):
            for item, binding in zip(self.items, one["cases"], strict=True):
                capture = item["capture"]
                directory = self.out / item["id"]
                choice = batch.gate.prepare(self.elf, item["stdin"], capture, capture / "baseline-selection.json",
                                            capture / "policy.json", directory, 1, "warm-three")
                self.assertNotIn("calibration_window", choice)
                self.assertEqual(choice["policy"].name, "policy-2.json")
                self.assertEqual(binding["packet_sha256"], batch.elf_digest(choice["packet"]))
                self.assertEqual(binding["selection_sha256"], batch.elf_digest(choice["selection"]))
                self.assertEqual((directory / "policy-0.json").read_bytes(), (capture / "policy.json").read_bytes())
            with self.assertRaisesRegex(ValueError, "manifest"):
                batch.prepare(self.items[::-1], self.out)

    def test_gate_requires_existing_batch_member_without_paying_again(self):
        with patch.object(batch.anchor, "native_once", side_effect=self.sample):
            batch.prepare(self.items, self.out)
        item = self.items[0]
        case_id = item["id"]
        packet = self.out / case_id
        manifest_path = self.out / "manifest.json"
        manifest = json.loads(manifest_path.read_bytes())

        def gate_read(directory=packet, strategy="warm-three", manifest=manifest_path):
            cap = item["capture"]
            return batch.gate.prepare(self.elf, item["stdin"], cap, cap / "baseline-selection.json",
                                      cap / "policy.json", directory, 1, strategy, batch_manifest=manifest)

        with patch.object(batch.anchor, "native_once", side_effect=AssertionError("unexpected calibration payment")):
            bound = gate_read()
            self.assertEqual(bound["batch_case_id"], case_id)
            self.assertEqual(bound["batch_manifest_sha256"], batch.elf_digest(manifest_path))
            self.assertEqual(bound["policy"].name, "policy-2.json")
            self.assertEqual(bound["prediction_ns"], json.loads(bound["policy"].read_bytes())["native"]["cpu_ns"])
            missing = self.out / "missing"
            with self.assertRaisesRegex(ValueError, "member is missing"):
                gate_read(missing)
            self.assertFalse(missing.exists())
            with self.assertRaisesRegex(ValueError, "require the warm-three"):
                gate_read(strategy="exact-one")
            with self.assertRaises(OSError):
                gate_read(manifest=self.out / "absent.json")
            # A genuine manifest at another batch root cannot claim this packet.
            wrong = self.out.parent / "different"
            wrong.mkdir()
            wrong_manifest = wrong / "manifest.json"
            wrong_manifest.write_bytes(manifest_path.read_bytes())
            with self.assertRaises(ValueError):
                gate_read(manifest=wrong_manifest)
            for field in ("id", "elf_sha256", "stdin_sha256", "current_result_sha256", "source_policy_sha256",
                          "source_selection_sha256", "packet_sha256", "selection_sha256"):
                copy = json.loads(json.dumps(manifest))
                copy["cases"][0][field] = "other" if field == "id" else "0" * 64
                manifest_path.write_text(json.dumps(copy))
                with self.subTest(field=field), self.assertRaisesRegex(ValueError, "batch member"):
                    gate_read()
            for field, value in (("placement", dict(self.host, cpu=3)), ("strategy", "warm-three"), ("rounds", [])):
                manifest_path.write_text(json.dumps(dict(manifest, **{field: value})))
                with self.subTest(field=field), self.assertRaisesRegex(ValueError, "batch"):
                    gate_read()

    def test_calibration_window_uses_only_three_interleaved_observations(self):
        item = self.items[0]
        capture = item["capture"]
        initial = json.loads((capture / "policy.json").read_bytes())["native"]["cpu_ns"]
        low_ms = initial // 1_000_000 + 10
        high_ms = low_ms + 4
        def sample(elf, stdin):
            native = self.sample(elf, stdin)
            index = self.launched.count(self.launched[-1]) - 1
            ms = (low_ms + 2, high_ms, low_ms)[index]
            return dict(native, cpu_ns=ms * 1_000_000, user_us=ms * 1000, sys_us=0)

        with patch.object(batch.anchor, "native_once", side_effect=sample):
            batch.prepare(self.items, self.out)
        packet = self.out / item["id"]
        files = {path: path.read_bytes() for path in packet.iterdir()}
        with patch.object(batch.anchor, "native_once", side_effect=AssertionError("unexpected calibration payment")):
            for ms, split in ((low_ms - 1, False), (low_ms, True), (high_ms - 1, True), (high_ms, False)):
                choice = batch.gate.prepare(self.elf, item["stdin"], capture, capture / "baseline-selection.json",
                                            capture / "policy.json", packet, ms, "warm-three",
                                            batch_manifest=self.out / "manifest.json")
                with self.subTest(ms=ms):
                    self.assertEqual(choice["calibration_window"],
                                     dict(min_ns=low_ms * 1_000_000, max_ns=high_ms * 1_000_000, split=split))
                    self.assertEqual(choice["prediction_ns"], (low_ms + 2) * 1_000_000)
                    self.assertEqual(choice["policy"], packet / "policy-1.json")
        self.assertEqual(files, {path: path.read_bytes() for path in packet.iterdir()})

    def test_refuses_changed_packet_and_stale_boot(self):
        with patch.object(batch.anchor, "native_once", side_effect=self.sample):
            batch.prepare(self.items, self.out)
        packet = self.out / self.names[0] / "packet.json"
        raw = json.loads(packet.read_bytes())
        raw["selected_index"] = 0
        packet.write_text(json.dumps(raw))
        with patch.object(batch.anchor, "native_once", side_effect=AssertionError("unexpected payment")):
            with self.assertRaisesRegex(ValueError, "differs from its evidence"):
                batch.prepare(self.items, self.out)
            self.host["boot_sha256"] = "a" * 64
            with self.assertRaisesRegex(ValueError, "CPU and boot"):
                batch.prepare(self.items, self.out)

    def test_rejects_reused_native_pid_across_inputs(self):
        def first_round_pid_collision(elf, stdin):
            native = self.sample(elf, stdin)
            if len(self.launched) == 2:
                native["pid"] -= 1
            return native

        with patch.object(batch.anchor, "native_once", side_effect=first_round_pid_collision):
            with self.assertRaisesRegex(ValueError, "duplicate capture or native PID"):
                batch.prepare(self.items, self.out)
        self.assertFalse(self.out.exists())

    def test_validates_every_source_before_starting_and_leaves_no_partial_batch(self):
        mismatched = [*self.items[:-1], dict(self.items[-1], stdin=self.items[0]["stdin"])]
        with patch.object(batch.anchor, "native_once", side_effect=AssertionError("unexpected payment")):
            with self.assertRaisesRegex(ValueError, "stdin differs"):
                batch.prepare(mismatched, self.out)
            with self.assertRaisesRegex(ValueError, "overlapping"):
                batch.prepare([self.items[0], self.items[0]], self.out)
            with self.assertRaisesRegex(ValueError, "distinct ELF/input"):
                batch.prepare([self.items[0], dict(self.items[0], id="alias")], self.out)
        def interrupted(elf, stdin):
            if len(self.launched) == 4:
                raise OSError("interrupted native run")
            return self.sample(elf, stdin)
        with patch.object(batch.anchor, "native_once", side_effect=interrupted):
            with self.assertRaisesRegex(OSError, "interrupted"):
                batch.prepare(self.items, self.out)
        self.assertFalse(self.out.exists())
        self.assertEqual(list(self.out.parent.iterdir()), [])


@unittest.skipUnless(all(os.getenv(k) for k in ("DURANTE_SHAPE_RUNTIME", "DURANTE_SHAPE_BINARIES", "DURANTE_TEMPORAL_PROBE")),
                     "requires registered live runtime, Fenwick ELF and temporal probe")
class BatchGateCommand(unittest.TestCase):
    def test_gate_cli_consumes_the_published_interleaved_member(self):
        from shape_calibration import combined, select_policy

        runtime = Path(os.environ["DURANTE_SHAPE_RUNTIME"])
        elf = Path(os.environ["DURANTE_SHAPE_BINARIES"]) / "fenwick"
        probe = Path(os.environ["DURANTE_TEMPORAL_PROBE"])
        affinity = os.sched_getaffinity(0)
        self.addCleanup(os.sched_setaffinity, 0, affinity)
        os.sched_setaffinity(0, {min(affinity)})
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            items = []
            for i, source in enumerate(("16384 300000\n", "32768 400000\n")):
                case_id = f"fenwick/input-{i}"
                stdin = root / f"input-{i}"
                capture = root / f"capture-{i}"
                stdin.write_text(source)
                combined.capture(runtime, probe, elf, stdin, capture)
                selection = select_policy.resolve(elf, capture, [], batch.elf_digest(probe), "footprint")
                batch.anchor.save_new(capture / "baseline-selection.json", selection)
                items.append(dict(id=case_id, elf=str(elf), capture=str(capture), stdin=str(stdin)))
            group = root / "batch"
            items_file = root / "items.json"
            items_file.write_text(json.dumps(items))
            subprocess.run([sys.executable, "-B", batch.__file__, "--items", items_file, "--out", group],
                           check=True, capture_output=True, timeout=90)
            first = items[0]
            packet = group / first["id"]
            capture = Path(first["capture"])
            out = root / "decision"
            command = [sys.executable, "-B", batch.gate.__file__, "--strategy", "warm-three", "--payload", runtime,
                       "--elf", elf, "--stdin", first["stdin"], "--capture", capture, "--policy", capture / "policy.json",
                       "--selection", capture / "baseline-selection.json", "--packet-dir", packet,
                       "--batch-manifest", group / "manifest.json", "--limit-ms", "10", "--out", out]
            proc = subprocess.run(command, capture_output=True, timeout=60)
            self.assertIn(proc.returncode, (0, 123), (proc.stdout, proc.stderr))
            decision = json.loads(proc.stdout)
            self.assertIn(decision["status"], ("within_budget", "budget_exceeded"))
            receipt = json.loads((out / "gate.json").read_bytes())
            selected = json.loads((packet / "selection.json").read_bytes())
            self.assertEqual(receipt["batch_case_id"], first["id"])
            self.assertEqual(receipt["batch_manifest_sha256"], batch.elf_digest(group / "manifest.json"))
            times = [json.loads((packet / f"policy-{i}.json").read_bytes())["native"]["cpu_ns"] for i in (1, 2, 3)]
            self.assertEqual(receipt["calibration_window"],
                             dict(min_ns=min(times), max_ns=max(times), split=min(times) <= 10_000_000 < max(times)))
            self.assertEqual(decision["preflight_prediction_ns"], selected["prediction_ns"])
            self.assertEqual(receipt["selected_policy_sha256"], selected["policy_sha256"])
