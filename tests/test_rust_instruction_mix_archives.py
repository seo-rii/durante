import subprocess
import sys
from pathlib import Path
import unittest


class ArchivedRustMeasurements(unittest.TestCase):
    def test_public_results_replay(self):
        root = Path(__file__).resolve().parents[1]
        study = root / "experiments/rust_instruction_mix"
        results = root / "experiments/results/gce-rust-instruction-mix"
        for folder, scorer in ((results, study / "score.py"),
                               (results / "holdout", study / "holdout/score_holdout.py")):
            dataset = folder / "dataset.json.gz"
            for script, args in (
                (scorer, ["--verify", folder / "evaluation.json"]),
                (study / "placement.py", [folder / "placement.json.gz", "--platform", folder / "platform.json"]),
                (study / "verify_archive.py", [folder / "samples.tar.xz", "--probe-build", results / "probe-build.json"]),
            ):
                with self.subTest(study=folder.name, script=script.name):
                    run = subprocess.run([sys.executable, script, dataset, *args], cwd=root, text=True,
                                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
                    self.assertEqual(run.returncode, 0, run.stdout)
