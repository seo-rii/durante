from fractions import Fraction
import json
from pathlib import Path, PurePosixPath
import shutil
import sys
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments"))
from calibrated_limits.verify import verify

RESULT = ROOT / "experiments/results/gce-calibrated-limits"


class CalibratedLimitReplay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        with tarfile.open(RESULT / "output.tar.xz", "r:xz") as archive:
            for member in archive:
                path = PurePosixPath(member.name)
                if not path.parts or path.parts[0] != "output" or ".." in path.parts:
                    raise ValueError("invalid archived receipt path")
                destination = Path(cls.temp.name) / path
                if member.isdir():
                    destination.mkdir(parents=True, exist_ok=True)
                elif member.isfile():
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with archive.extractfile(member) as src, destination.open("xb") as dest:
                        shutil.copyfileobj(src, dest)
                else:
                    raise ValueError("unexpected archived receipt type")
        cls.output = Path(cls.temp.name) / "output"

    def test_published_raw_runs_reproduce_decisions(self):
        actual = verify(self.output)
        expected = json.loads((RESULT / "metrics.json").read_bytes())
        self.assertEqual(expected, actual)
        self.assertEqual(actual["native_runs"], 108)
        self.assertEqual(actual["totals"]["all"]["limits"], 32)
        self.assertEqual(actual["pids_verified"], 7 * 2 + 108 + 44)
        cert = actual["within_elf_scale_bound"]
        ratio = Fraction(cert["work_ratio"])
        error = Fraction(cert["relative_error_lower_bound"])
        low, high = cert["denominator_min_native_ns"], cert["numerator_max_native_ns"]
        self.assertEqual(ratio * low * (1 - error) - ratio, high * (1 + error))
        self.assertGreater(error, Fraction(1, 5))
        self.assertTrue(cert["excludes_20pct"])

    def test_corrupted_output_is_rejected(self):
        stdout = self.output / "native/000/stdout"
        original = stdout.read_bytes()
        try:
            stdout.write_bytes(original + b"corrupt")
            with self.assertRaisesRegex(ValueError, "wrong direct native output"):
                verify(self.output)
        finally:
            stdout.write_bytes(original)

    def test_crossing_snapshot_must_support_live_decision(self):
        for directory in sorted((self.output / "live").iterdir()):
            crossing = directory / "crossing.bin"
            if crossing.is_file():
                break
        else:
            self.fail("the published capture contains no actual live stop")
        original = crossing.read_bytes()
        try:
            crossing.write_bytes(bytes(len(original)))
            with self.assertRaisesRegex(ValueError, "uninitialized, invalidated or overflowing clock"):
                verify(self.output)
        finally:
            crossing.write_bytes(original)
