import copy
import importlib.util
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
STUDY = ROOT / "experiments/access_sample"
RESULT = ROOT / "experiments/results/gce-access-sample"
spec = importlib.util.spec_from_file_location("access_sample_study", STUDY/"study.py")
study = importlib.util.module_from_spec(spec)
spec.loader.exec_module(study)


class SampleReceipts(unittest.TestCase):
    def test_zero_and_single_visit_sites(self):
        empty = {"schema": 1, "pid": 743, "threads": 1, "period": 1024, "line_bytes": 64,
                 "loads": 0, "pairs": 0, "near_pairs": 0, "far_pairs": 0, "sites": []}
        study.validate_sample(empty, 743)
        site = {"offset": 22, "loads": 1025, "pairs": 1, "near_pairs": 1, "far_pairs": 0}
        observed = dict(empty, loads=1025, pairs=1, near_pairs=1, sites=[site])
        study.validate_sample(observed, 743)
        for description, change in (
            ("extra cadence count", lambda s: s["sites"][0].update(pairs=2, near_pairs=2)),
            ("split mismatch", lambda s: s["sites"][0].update(far_pairs=1)),
            ("duplicate offset", lambda s: s["sites"].append(s["sites"][0])),
            ("wrong PID", lambda s: s.update(pid=744)),
            ("wrong period", lambda s: s.update(period=True)),
            ("overflow", lambda s: s.update(loads=2**64)),
            ("site total mismatch", lambda s: s.update(near_pairs=0)),
            ("unused site", lambda s: s["sites"][0].update(loads=0)),
        ):
            with self.subTest(description=description):
                wrong = copy.deepcopy(observed)
                change(wrong)
                with self.assertRaises(ValueError):
                    study.validate_sample(wrong, 743)
        with self.assertRaises(ValueError):
            study.read_sample(b'{"sites":[{"loads":1,"loads":2}]}')

    def test_public_output_records_sampler_and_placement_replay(self):
        dataset = RESULT/"dataset.json.gz"
        for name, rest in (("verify_receipts.py", [RESULT/"receipts.tar.xz"]),
                           ("verify_placement.py", [RESULT/"placement.json.gz", "--platform", RESULT/"platform.json"])):
            with self.subTest(script=name):
                run = subprocess.run([sys.executable, STUDY/name, dataset, *rest], cwd=ROOT,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=60)
                self.assertEqual(run.returncode, 0, run.stdout)


if __name__ == "__main__":
    unittest.main()
