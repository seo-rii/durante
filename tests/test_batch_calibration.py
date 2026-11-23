"""Check round scheduling and immutable batch publication with authentic complete capture receipts."""
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
from pathlib import Path
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
                self.assertEqual(choice["policy"].name, "policy-2.json")
                self.assertEqual(binding["packet_sha256"], batch.elf_digest(choice["packet"]))
                self.assertEqual(binding["selection_sha256"], batch.elf_digest(choice["selection"]))
                self.assertEqual((directory / "policy-0.json").read_bytes(), (capture / "policy.json").read_bytes())
            with self.assertRaisesRegex(ValueError, "manifest"):
                batch.prepare(self.items[::-1], self.out)

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
