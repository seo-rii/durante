import json
from pathlib import Path
import runpy
import tempfile
import unittest

runner = runpy.run_path(str(Path(__file__).resolve().parents[1] / "experiments/guarded_sampler/run.py"))


class GuardedRunner(unittest.TestCase):
    def test_empty_calibration_is_valid_only_with_zero_totals(self):
        sample = dict(schema=2, pid=21, threads=1, period=1024, line_bytes=64, loads=0, pairs=0,
                      near_pairs=0, far_pairs=0, sites=[])
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "sample.json").write_text(json.dumps(sample))
            self.assertIsNone(runner["full_sample"](directory, 21))
            for changed in (dict(loads=9), dict(pairs=False), dict(threads=2)):
                (directory / "sample.json").write_text(json.dumps({**sample, **changed}))
                with self.assertRaises(ValueError):
                    runner["full_sample"](directory, 21)

    def test_concurrent_first_use_preserves_the_first_complete_profile(self):
        sample = dict(schema=2, pid=21, threads=1, period=1024, line_bytes=64, loads=1, pairs=0,
                      near_pairs=0, far_pairs=0)
        site = dict(offset=11, sampled_address_span_bytes=0, loads=1, pairs=0, near_pairs=0, far_pairs=0)
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "cached.json"
            for offset in (11, 12):
                raw = json.dumps({**sample, "sites": [{**site, "offset": offset}]}).encode()
                profile = runner["capture_for_digest"]("a" * 64, b"input", raw, 21, arm="full")
                runner["publish_profile"](dest, profile)
            self.assertEqual(json.loads(dest.read_bytes())["offsets"], [11])
