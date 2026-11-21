import gzip
import json
from pathlib import Path
import runpy
import subprocess
import sys
import tarfile
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[1]
STUDY = ROOT / "experiments/divide_clock"
RESULT = ROOT / "experiments/results/divide-clock"


class DividerArchive(unittest.TestCase):
    def test_two_local_feature_replays_against_archived_gce_counters(self):
        subprocess.run([sys.executable, STUDY/"coverage.py", RESULT/"observations.json.gz", RESULT/"receipts.tar.xz",
                        "--verify", RESULT/"coverage.json"], cwd=ROOT, check=True, capture_output=True, timeout=60)

    def test_gce_baselines_match_the_original_meter_streams(self):
        from_archive = runpy.run_path(str(STUDY/"corpus.py"))
        original_tools = runpy.run_path(str(ROOT/"experiments/rust_operand_bytes/collect.py"))
        project = original_tools["production"]
        reader = runpy.run_path(str(ROOT/"collect.py"))["collect"]
        checked = 0
        for study, folder, names in from_archive["PRIOR"]:
            base = ROOT/"experiments/results"/folder
            rust = study == "access_sample"
            phase = "baseline" if rust else "operand_bytes"
            data = json.loads(gzip.decompress((base/"dataset.json.gz").read_bytes()))
            with tarfile.open(base/("receipts.tar.xz" if rust else "samples.tar.xz")) as tar:
                for i,row in enumerate(data["samples"], 1):
                    name = row["workload_id"]
                    if name not in names:
                        continue
                    obs = row[phase]
                    stream = f'process-{obs["pid"]}.jsonl'
                    member = f'samples/{i:02d}-{name}-{row["repetition"]}/{phase}/records/{stream}'
                    with TemporaryDirectory() as tmp:
                        records = Path(tmp)
                        (records/stream).write_bytes(from_archive["read_member"](tar, member))
                        self.assertEqual(project(records, obs["pid"], "operand_bytes", reader), obs["production"])
                    checked += 1
        self.assertEqual(checked, 56)


if __name__ == "__main__":
    unittest.main()
