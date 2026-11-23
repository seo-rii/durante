from fractions import Fraction
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments"))
from shape_calibration import aonohako_clock as bridge


class DeadlineProjection(unittest.TestCase):
    def test_count_matches_exhaustive_small_triangles(self):
        for a in range(1, 10):
            for b in range(1, 13):
                for budget in range(40):
                    count = sum(a * i + b * r <= budget for i in range(budget // a + 1)
                                for r in range(budget // b + 1))
                    self.assertEqual(bridge.count_within(a, b, budget), count)

    def test_exact_small_clock_and_large_tied_count(self):
        self.assertEqual(bridge.project(Fraction(3, 7), Fraction(5, 14), [10, 1]), (6, 5, 14))
        k = 2**100
        self.assertEqual(bridge.count_within(3, 3, 3 * k), (k + 1) * (k + 2) // 2)
        self.assertEqual(bridge.count_within(3, 3, 3 * k - 1), k * (k + 1) // 2)

    def test_nonrepresentable_weights_preserve_both_sides_of_boundary(self):
        epsilon = Fraction(1, 2**80)
        left, right = 250_000 + epsilon, 125_000 + 3 * epsilon
        limits = [1, 2, 4]
        with patch.object(bridge, "SEARCH_DENOMINATORS", 2048):
            a, b, d = bridge.project(left, right, limits)
        self.assertTrue(all(0 < n <= bridge.U64_MAX for n in (a, b, d)))
        for ms in limits:
            for i in range(22):
                for r in range(44):
                    self.assertEqual(left * i + right * r > ms * 1_000_000,
                                     a * i + b * r > ms * 1_000_000 * d)

    def test_rejects_uncertified_unrepresentable_and_invalid(self):
        with patch.object(bridge, "SEARCH_DENOMINATORS", 16):
            with self.assertRaisesRegex(ValueError, "no certified"):
                bridge.project(Fraction(1, 2**70), Fraction(1), [1])
        with self.assertRaisesRegex(ValueError, "range"):
            bridge.project(Fraction(2**64), Fraction(1), [1])
        for bad in (Fraction(0), Fraction(-1), Fraction(1, 2**1024)):
            with self.assertRaisesRegex(ValueError, "size or sign"):
                bridge.project(bad, Fraction(2), [1])
        for limits in ([], [True], [0], [1, 1], [2**63 // 1_000_000 + 1], list(range(1, 18))):
            with self.assertRaisesRegex(ValueError, "limits"):
                bridge.project(Fraction(1), Fraction(1), limits)


class SelectedProjection(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        cls.root = Path(tmp.name)
        archive_path = ROOT / "experiments/results/gce-shape-calibration/output.tar.xz"
        with tarfile.open(archive_path) as archive:
            for item in archive:
                path = Path(item.name)
                if item.isfile() and path.parts[:4] == ("output", "cases", "fenwick", "long") and ".." not in path.parts:
                    local = cls.root.joinpath(*path.parts[3:])
                    local.parent.mkdir(parents=True, exist_ok=True)
                    local.write_bytes(archive.extractfile(item).read())
                elif item.name == "output/cases/fenwick/train/policy.json":
                    cls.policy = cls.root / "policy.json"
                    cls.policy.write_bytes(archive.extractfile(item).read())
        cls.capture = cls.root / "long"
        cls.selection = cls.root / "selection.json"
        selected = json.loads((cls.capture / "selection.json").read_bytes())["temporal"]
        cls.selection.write_text(json.dumps(selected))
        cls.stdin = cls.capture / "full/stdin"

    def attempt(self, stdin, selection, policy):
        binary_dir = os.environ.get("DURANTE_SHAPE_BINARIES")
        if binary_dir:
            return bridge.export(selection, self.capture, policy, Path(binary_dir) / "fenwick", stdin, [300, 25, 75, 150])
        # The registered archive identifies the ELF but omits it. Only substitute its read-only digest.
        elf_sha = json.loads(self.policy.read_bytes())["anchor"]["elf_sha256"]
        with patch.object(bridge.anchor, "static_elf", return_value=elf_sha):
            return bridge.export(selection, self.capture, policy, self.root / "fenwick", stdin, [300, 25, 75, 150])

    def test_archive_receipt_and_counter_decisions(self):
        receipt = self.attempt(self.stdin, self.selection, self.policy)
        self.assertEqual(receipt["action"], "reuse")
        self.assertEqual(receipt["limits_ms"], [25, 75, 150, 300])
        self.assertEqual(receipt["authority"], "diagnostic_only")
        for field, path in (("policy_sha256", self.policy), ("selection_sha256", self.selection),
                            ("stdin_sha256", self.stdin)):
            self.assertEqual(receipt[field], bridge.elf_digest(path))
        w = receipt["weights"]
        self.assertEqual(w["method"], "weighted-work-nanoseconds-v1")
        for snapshot in sorted((self.capture / "live").glob("*/result.json")):
            row = json.loads(snapshot.read_bytes())
            progress = row["progress"]
            total = (progress["instructions"] * w["instruction_ns_numerator"]
                     + (progress["movs_bytes"] + progress["stos_bytes"]) * w["rep_byte_ns_numerator"])
            self.assertEqual(total > row["limit_ms"] * 1_000_000 * w["ns_denominator"],
                             row["status"] == "budget_exceeded")

    def test_refuses_swapped_input_selection_and_unpinned_calibration(self):
        with self.assertRaisesRegex(ValueError, "requested stdin"):
            self.attempt(self.selection, self.selection, self.policy)
        changed = json.loads(self.selection.read_bytes())
        changed["policy_sha256"] = "0" * 64
        bad = self.root / "changed.json"
        bad.write_text(json.dumps(changed))
        with self.assertRaisesRegex(ValueError, "selection differs"):
            self.attempt(self.stdin, bad, self.policy)
        policy = json.loads(self.policy.read_bytes())
        policy["native"].update(affinity=[0, 1], single_cpu=False)
        policy["calibration_sha256"] = bridge.anchor.calibration_digest(policy["anchor"], policy["native"])
        bad.write_text(json.dumps(policy))
        with self.assertRaisesRegex(ValueError, "single-CPU"):
            self.attempt(self.stdin, self.selection, bad)
