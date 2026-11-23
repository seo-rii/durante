from copy import deepcopy
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
from footprint_admission import admission
from shape_calibration import run, select_policy, temporal


def profile(*sites):
    return {"observation": {"mode": "full", "elf_sha256": "a" * 64, "sample_sha256": "b" * 64},
            "sites": [{"offset": offset, "pairs": pairs, "loads": pairs * 1024, "far_pairs": 0,
                       "sampled_address_span_bytes": span} for offset, (pairs, span) in enumerate(sites)]}


class FootprintRule(unittest.TestCase):
    def test_symmetric_inclusive_ratio_and_large_span_threshold(self):
        mib = 1 << 20
        for a, b, status in ((mib, mib * 3 // 2, "reuse"), (mib, mib * 3 // 2 + 1, "needs_native"),
                             (0, mib - 1, "reuse"), (0, mib, "needs_native")):
            for left, right in ((a, b), (b, a)):
                with self.subTest(left=left, right=right):
                    receipt = admission.compare(profile((100, left)), profile((100, right)))
                    self.assertEqual(receipt["status"], status)

    def test_material_in_either_profile_and_missing_evidence(self):
        mib = 1 << 20
        reference = profile((950, mib), (50, 2*mib))
        current = profile((951, mib), (49, 4*mib))
        for before, after in ((reference, current), (current, reference)):
            self.assertEqual(admission.compare(before, after)["reasons"], ["large_span_changed"])
        minor = profile((951, mib), (49, 2*mib))
        self.assertEqual(admission.compare(minor, current)["status"], "reuse")
        self.assertEqual(admission.compare(profile((1000, mib)), reference)["reasons"], ["missing_material_site"])
        self.assertEqual(admission.compare(profile(), reference)["reasons"], ["no_material_sites"])


class PriorFenwick(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        cls.root = Path(tmp.name)
        with tarfile.open(ROOT / "experiments/results/gce-shape-calibration/output.tar.xz") as archive:
            prefix = "output/cases/fenwick/"
            for item in archive:
                if item.isfile() and item.name.startswith(prefix):
                    rel = Path(item.name[len(prefix):])
                    assert not rel.is_absolute() and ".." not in rel.parts
                    dest = cls.root / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(archive.extractfile(item).read())
        cls.elf_sha = json.loads((cls.root / "train/policy.json").read_bytes())["anchor"]["elf_sha256"]

    def resolve(self, current, method, refs, elf=None):
        return select_policy.resolve(elf or self.root / "fixture", self.root / current,
                                     [self.root / r for r in refs], temporal.PROBE_SHA, method)

    def test_original_decisions_and_expanded_fallback(self):
        refs = ["train", "large"]
        current = self.root / "expanded"
        original = json.loads((current / "selection.json").read_bytes())
        with patch.object(select_policy.anchor, "static_elf", return_value=self.elf_sha):
            for method in ("shape", "temporal"):
                self.assertEqual(self.resolve("expanded", method, refs), original[method])
            # Return the independently tested fallback's clock so we can inspect every rejected candidate.
            substitute = select_policy.anchor.load_clock(self.root / "large/policy.json", self.root / "fixture")
            with patch.object(select_policy, "current_clock", return_value=substitute) as fallback:
                result = self.resolve("expanded", "footprint", refs)
        fallback.assert_called_once()
        self.assertEqual((result["action"], result["reference_index"]), ("calibrate", None))
        last = result["comparisons"][1]
        self.assertEqual((last["shape"]["status"], last["temporal"]["status"]), ("reuse", "reuse"))
        self.assertEqual(last["footprint"]["reasons"], ["large_span_changed"])
        self.assertEqual([(s["offset"], s["reference_span_bytes"], s["current_span_bytes"])
                          for s in last["footprint"]["material_sites"]],
                         [(6355, 4193388, 8385672), (6370, 4192256, 8387032)])

    def test_reused_policy_and_corrupted_receipts(self):
        current = self.root / "long"
        policy = self.root / "train/policy.json"
        with patch.object(select_policy.anchor, "static_elf", return_value=self.elf_sha):
            decision = self.resolve("long", "footprint", ["train"])
            clock = select_policy.anchor.load_clock(policy, self.root / "fixture")
        full, extra = select_policy.profiles(current, self.elf_sha, temporal.PROBE_SHA, "footprint")
        def check(sel):
            run.check_selection(sel, full, extra, json.loads(policy.read_bytes()), clock)
        self.assertEqual((decision["action"], decision["reference_index"]), ("reuse", 0))
        check(decision)
        for change in ("method", "reference", "current", "site", "reference_span", "current_span", "status"):
            with self.subTest(change=change):
                invalid = deepcopy(decision)
                receipt = invalid["comparisons"][0]["footprint"]
                if change == "method":
                    invalid["method"] = "temporal"
                elif change in ("reference", "current"):
                    receipt[change + "_sample_sha256"] = "0" * 64
                elif change == "site":
                    receipt["material_sites"].pop()
                elif change in ("reference_span", "current_span"):
                    receipt["material_sites"][0][change + "_bytes"] += 1
                else:
                    receipt["status"] = "needs_native"
                with self.assertRaises(ValueError):
                    check(invalid)

    @unittest.skipUnless(os.getenv("DURANTE_SHAPE_RUNTIME") and os.getenv("DURANTE_SHAPE_BINARIES"),
                         "requires the registered #82 runtime and #85 binaries")
    def test_expanded_native_fallback_enforces_live_limits(self):
        current = self.root / "expanded"
        elf = Path(os.environ["DURANTE_SHAPE_BINARIES"]) / "fenwick"
        decision = self.resolve("expanded", "footprint", ["train", "large"], elf)
        self.assertEqual((decision["action"], decision["reference_index"]), ("calibrate", None))
        selection = self.root / "expanded-selection.json"
        selection.write_text(json.dumps(decision))
        with patch.object(select_policy.anchor, "native_once", side_effect=AssertionError("duplicate native run")):
            self.assertEqual(decision, self.resolve("expanded", "footprint", ["train", "large"], elf))
        for limit, expected in ((1, "budget_exceeded"), (3000, "within_budget")):
            result = run.run(Path(os.environ["DURANTE_SHAPE_RUNTIME"]), selection, current, current / "policy.json",
                             elf, current / "full/stdin", self.root / f"live-{limit}", limit)
            self.assertEqual(result["status"], expected, result)
            self.assertGreater(result["pid"], 0)
