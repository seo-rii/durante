import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments"))
from shape_calibration.shape import compare, inspect_run


def profile(*rows):
    sites = [dict(offset=i, pairs=pairs, loads=pairs * 1024, near_pairs=pairs-far,
                  far_pairs=far, sampled_address_span_bytes=span)
             for i, (pairs, far, span) in enumerate(rows)]
    return {"observation": {"mode": "full", "elf_sha256": "a" * 64, "sample_sha256": "b" * 64}, "sites": sites}


class FullAddressShape(unittest.TestCase):
    def test_relative_site_shares_and_exact_variation_boundary(self):
        base = profile((192, 48, 4096), (64, 16, 8192))
        longer = profile((384, 96, 4096), (128, 32, 8192))
        self.assertEqual(compare(base, longer)["status"], "reuse")
        boundary = compare(base, profile((160, 40, 4096), (96, 24, 8192)))
        self.assertEqual((boundary["total_variation"], boundary["status"]), ("1/8", "reuse"))
        change = compare(base, profile((159, 40, 4096), (97, 24, 8192)))
        self.assertEqual(change["reasons"], ["site_shares_changed"])

    def test_page_floor_span_ratio_and_far_fraction_have_inclusive_boundaries(self):
        base = profile((128, 0, 0))
        self.assertEqual(compare(base, profile((128, 8, 16384)))["status"], "reuse")
        for reference, current in ((base, profile((128, 9, 16385))), (profile((128, 9, 16385)), base)):
            changed = compare(reference, current)
            self.assertEqual(changed["reasons"], ["far_fraction_changed", "span_changed"])
            self.assertEqual(changed["material_sites"][0]["span_ratio"], "16385/4096")

    def test_absent_and_insufficient_evidence_requests_native(self):
        self.assertEqual(compare(profile(), profile((256, 1, 4096)))["reasons"], ["empty_full_sample"])
        thin = profile((63, 0, 256))
        self.assertEqual(compare(thin, thin)["reasons"], ["insufficient_pairs"])
        # 21 equally common sites are all below the material-site threshold.
        diffuse = profile(*[(256, 4, 64)] * 21)
        self.assertEqual(compare(diffuse, diffuse)["reasons"], ["insufficient_material_coverage"])
        old = profile((1000, 0, 0))
        gained = profile((950, 0, 0), (50, 0, 0))
        self.assertEqual(compare(old, gained)["reasons"], ["insufficient_pairs"])

    def test_incompatible_executable_or_selected_sampler_is_rejected(self):
        reference, other = profile((128, 1, 64)), profile((128, 1, 64))
        for key, value in (("elf_sha256", "c" * 64), ("mode", "guarded")):
            with self.subTest(key=key):
                changed = {**other, "observation": {**other["observation"], key: value}}
                with self.assertRaisesRegex(ValueError, "incompatible"):
                    compare(reference, changed)

    def test_real_full_run_and_wrong_pid(self):
        archive = ROOT / "experiments/results/gce-calibrated-limits/output.tar.xz"
        prefix = "output/first/integer/"
        with tempfile.TemporaryDirectory() as temp, tarfile.open(archive, "r:xz") as tar:
            run = Path(temp)
            for m in tar:
                if m.isfile() and m.name.startswith(prefix):
                    relative = Path(m.name[len(prefix):])
                    self.assertFalse(relative.is_absolute() or ".." in relative.parts)
                    dest = run / relative
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(tar.extractfile(m).read())
            result = json.loads((run / "result.json").read_bytes())
            seen = inspect_run(run, result["elf_sha256"])
            self.assertEqual(seen["observation"]["pid"], result["launches"][0]["launcher_pid"])
            sample = run / "full/sample.json"
            changed = json.loads(sample.read_bytes())
            changed["pid"] += 1
            sample.write_text(json.dumps(changed))
            with self.assertRaisesRegex(ValueError, "sampler identity"):
                inspect_run(run, result["elf_sha256"])
