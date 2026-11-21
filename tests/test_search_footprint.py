"""Replay native timings and exercise the offline predictor with original per-process records."""

import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
STUDY = ROOT / "experiments/search_footprint"
DATA = ROOT / "experiments/results/gce-search-footprint"


class FootprintStudy(unittest.TestCase):
    def checked(self, *command):
        proc = subprocess.run(command, capture_output=True, text=True, timeout=150)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return proc.stdout

    def test_registered_inputs_placement_and_scores(self):
        self.checked(sys.executable, STUDY / "fixtures.py")
        raw = DATA / "dataset.json.gz"
        self.checked(sys.executable, STUDY / "verify.py", raw, DATA / "receipts.tar.xz")
        self.checked(
            sys.executable,
            STUDY / "placement.py",
            raw,
            DATA / "placement.json.gz",
            "--platform",
            DATA / "platform.json",
        )
        self.checked(sys.executable, STUDY / "score.py", "--verify")

    def test_nonuniform_and_out_of_range_samples_abstain(self):
        # Run isolated so this evaluator's "model" never collides with another study.
        code = """
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1])))
from model import DATA, estimate, uniform_distance
profile = json.loads((DATA / 'parameters.json').read_bytes())
hist = [1000] + [0] * 60
sample = dict(pc=profile['terminal_offset'], pairs=1000, suffix_histogram=hist,
              sampled_min_index=0, sampled_max_index=262143)
assert uniform_distance(sample) > .9
assert estimate({'sample': sample}, profile['binary_sha256'], profile) == (None, 'endpoint_distribution')
sample['sampled_max_index'] = 15
assert uniform_distance(sample) == 0
assert estimate({'sample': sample}, profile['binary_sha256'], profile) == (None, 'outside_calibrated_span')
sample['pairs'] = 1
assert estimate({'sample': sample}, profile['binary_sha256'], profile) == (None, 'too_few_pairs')
"""
        self.checked(sys.executable, "-c", code, STUDY)

    def test_pending_sample_bounds_and_flags(self):
        sdk = Path(
            os.environ.get(
                "DURANTE_TEST_SDK", ROOT / "toolkit/sdk/DynamoRIO-Linux-11.3.0-1"
            )
        ).resolve()
        launcher = sdk / "bin64/drrun"
        if os.environ.get("DURANTE_TEST_BUNDLE"):
            launcher = Path(os.environ["DURANTE_TEST_BUNDLE"]) / "dynamorio/bin64/drrun"
        if (
            platform.machine() != "x86_64"
            or not (sdk / "cmake").is_dir()
            or not launcher.is_file()
        ):
            self.skipTest("requires an x86-64 DynamoRIO SDK and launcher")
        with tempfile.TemporaryDirectory() as tmp:
            self.checked(
                "cmake",
                "-S",
                STUDY / "probe",
                "-B",
                tmp,
                "-DCMAKE_BUILD_TYPE=Release",
                f"-DDynamoRIO_DIR={sdk}/cmake",
            )
            self.checked("cmake", "--build", tmp, "--target", "footprint_probe")
            self.checked(
                sys.executable,
                STUDY / "probe/check.py",
                launcher,
                Path(tmp) / "libfootprint_probe.so",
            )

    def test_predictor_reads_raw_same_process_records_and_rejects_another_elf(self):
        name = "new_384k"
        info = next(
            c
            for c in json.loads((DATA / "fixtures.json").read_bytes())["cases"]
            if c["workload_id"] == name
        )
        score = json.loads((DATA / "evaluation.json").read_bytes())["cases"][name][
            "models"
        ]["footprint"]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            elf = root / "Main"
            with tarfile.open(ROOT / info["binary_archive"]) as t:
                elf.write_bytes(t.extractfile(info["archive_member"]).read())
            with tarfile.open(DATA / "receipts.tar.xz") as t:
                sample = next(
                    m
                    for m in t.getnames()
                    if m.endswith(f"-{name}-1/sampled/sample.json")
                )
                raw = t.extractfile(sample).read()
                (root / "sample.json").write_bytes(raw)
                pid = json.loads(raw)["pid"]
                record = f"process-{pid}.jsonl"
                records = root / "records"
                records.mkdir()
                (records / record).write_bytes(
                    t.extractfile(
                        sample.rsplit("/", 1)[0] + "/records/" + record
                    ).read()
                )
            command = [
                sys.executable,
                STUDY / "estimate.py",
                "--elf",
                elf,
                "--records",
                records,
                "--sample",
                root / "sample.json",
            ]
            result = json.loads(self.checked(*command))
            self.assertEqual(result["estimated_ns"], score["ns"][0])
            self.assertEqual(result["status"], "eligible")
            elf.write_bytes(b"other")
            result = json.loads(self.checked(*command))
            self.assertIsNone(result["estimated_ns"])
            self.assertEqual(result["status"], "unregistered_executable_or_site")
