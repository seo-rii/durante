import copy
import gzip
import importlib.util
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
STUDY = ROOT / "experiments/dependency_pairs"
RESULTS = ROOT / "experiments/results/gce-dependency-pairs"
spec = importlib.util.spec_from_file_location("dependency_pair_study", STUDY / "study.py")
study = importlib.util.module_from_spec(spec)
spec.loader.exec_module(study)


class DependencyPairReceipts(unittest.TestCase):
    def test_build_manifest_matches_the_frozen_kernels_and_oracles(self):
        for relative in (Path(), Path("followup")):
            plan = study.read_json(STUDY / relative / "protocol.json")
            manifest = RESULTS / relative / "fixtures.json"
            self.assertEqual(study.sha(manifest.read_bytes()), plan["fixture_manifest_sha256"])
            for built, frozen in zip(study.read_json(manifest)["cases"], plan["cases"], strict=True):
                self.assertEqual(built, {k: frozen[k] for k in built})

    def test_second_measurement_is_bound_to_the_completed_first_on_a_new_guest(self):
        plan = study.read_json(STUDY / "followup/protocol.json")
        self.assertEqual(plan["primary_protocol_sha256"], study.sha((STUDY / "protocol.json").read_bytes()))
        self.assertEqual(plan["primary_dataset_sha256"], study.sha(gzip.decompress((RESULTS / "dataset.json.gz").read_bytes())))
        old = study.read_json(RESULTS / "platform.json")["guest_id_sha256"]
        new = study.read_json(RESULTS / "followup/platform.json")["guest_id_sha256"]
        self.assertNotEqual(new, old)

    def test_waits_counters_outputs_binaries_and_cpu_placement_replay(self):
        for relative in (Path(), Path("followup")):
            results = RESULTS / relative
            for mode, primary, args in (
                ("receipts", "verify_receipts.py", [results / "samples.tar.xz"]),
                ("placement", "verify_placement.py", [results / "placement.json.gz", "--platform", results / "platform.json"]),
            ):
                with self.subTest(experiment=relative, mode=mode):
                    command = [STUDY / primary] if relative == Path() else [STUDY / relative / "replay.py", mode]
                    run = subprocess.run([sys.executable, *command, results / "dataset.json.gz", *args], cwd=ROOT,
                                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=60)
                    self.assertEqual(run.returncode, 0, run.stdout)

    def test_changed_schedule_cpu_time_or_counter_is_rejected(self):
        dataset = study.read_json(RESULTS / "dataset.json.gz")
        plan = study.load_protocol(dataset)
        for name in ("round", "phase-order", "reused-pid", "wait4", "counter-version", "binary"):
            with self.subTest(change=name):
                data = copy.deepcopy(dataset)
                row = data["samples"][0]
                if name == "round":
                    row["repetition"] += 1
                elif name == "phase-order":
                    row["phase_order"].reverse()
                elif name == "reused-pid":
                    row["operand_bytes"]["pid"] = row["native_before"]["pid"]
                elif name == "wait4":
                    row["native_after"]["system_ns"] += 1
                elif name == "counter-version":
                    row["operand_bytes"]["production"]["memory_operand_bytes"]["version"] = True
                elif name == "binary":
                    row["binary_sha256"] = "0"*64
                with self.assertRaises(ValueError):
                    list(study.validated_rows(data, plan))


if __name__ == "__main__":
    unittest.main()
