"""Check unseen-submission estimates and their pre-label seal against both GCE journals."""
from contextlib import contextmanager
from hashlib import sha256
import json
from pathlib import Path
from statistics import median
import sys
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments"))
from contest_submissions.verify import verify


def read(path):
    return json.loads(path.read_bytes())


@contextmanager
def replace(path, content):
    saved = path.read_bytes()
    path.write_bytes(content)
    try:
        yield
    finally:
        path.write_bytes(saved)


class SubmissionReplay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        cls.outputs = []
        for suffix in ("", "-repeat"):
            study = ROOT / "experiments/results" / ("gce-contest-submissions" + suffix)
            dest = Path(tmp.name) / ("boot" + suffix)
            with tarfile.open(study / "output.tar.gz", "r:gz") as archive:
                for member in archive:
                    path = Path(member.name)
                    if not path.parts or path.parts[0] != "output" or ".." in path.parts:
                        raise ValueError("unexpected archive path")
                    if member.isdir():
                        continue
                    if not member.isfile():
                        raise ValueError("unexpected archive member type")
                    target = dest / path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with target.open("xb") as stream:
                        stream.write(archive.extractfile(member).read())
            cls.outputs.append((dest / "output", study))

    def test_raw_native_votes_and_durations_on_distinct_boots(self):
        boots, payloads = set(), set()
        for root, study in self.outputs:
            run = read(root / "run.json")
            measured = verify(root)
            self.assertEqual(measured, read(study / "metrics.json"))
            collection = read(study / "collection.json")
            self.assertEqual(sha256((study / "output.tar.gz").read_bytes()).hexdigest(), collection["output_sha256"])
            self.assertEqual(collection["freeze"]["plan_sha256"], measured["plan_sha256"])
            payload = collection["freeze"]["payload_sha256"]
            self.assertRegex(payload, "^[0-9a-f]{64}$")
            payloads.add(payload)
            platform, cleanup = collection["platform"], collection["cleanup"]
            self.assertEqual((platform["cpu_platform"], platform["machine_type"], platform["threads_per_core"]),
                             ("AMD Milan", "n2d-standard-4", 1))
            self.assertEqual((cleanup["instance_http"], cleanup["boot_disk_http"]), (404, 404))
            self.assertIs(cleanup["key_removed"], True)
            boots.add(run["placement"]["boot_sha256"])
            self.assertEqual((measured["verified_pids"], measured["profile_runs"], measured["reference_native_runs"],
                              measured["submission_native_runs"]), (112, 16, 24, 72))
            predictions = read(root / "predictions.json")["predictions"]
            for name, case in measured["cases"].items():
                with self.subTest(guest=study.name, case=name):
                    timings = [(row["user_us"] + row["sys_us"]) * 1000 for row in run["rows"]
                               if row["case_id"] == name and row["role"] == "submission" and row["kind"] == "native"]
                    self.assertEqual(len(timings), 9)
                    self.assertEqual(median(timings), case["native_cpu"]["median_ns"])
                    for method, estimate in predictions[name].items():
                        self.assertEqual(estimate, case["predictions"][method])
                        for point in case["fixed_deadlines"][method]:
                            cutoff = point["limit_ms"] * 1_000_000
                            count = sum(value > cutoff for value in timings)
                            self.assertEqual(point["native_tle"], count)
                            self.assertEqual(point["majority_correct"], (estimate > cutoff) == (count >= 5))
        self.assertEqual(len(boots), 2)
        self.assertEqual(len(payloads), 1)

    def test_label_cannot_enter_prediction_journal(self):
        root, _ = self.outputs[0]
        run = read(root / "run.json")
        seal = read(root / "predictions.json")
        first = next(i for i, r in enumerate(run["rows"]) if r["kind"] == "native" and r["role"] == "submission")
        late = json.dumps(run["rows"][:first + 1], sort_keys=True, separators=(",", ":")).encode()
        seal["prior_rows_sha256"] = sha256(late).hexdigest()
        raw = json.dumps(seal).encode()
        run["predictions_sha256"] = sha256(raw).hexdigest()
        with replace(root / "run.json", json.dumps(run).encode()), replace(root / "predictions.json", raw):
            with self.assertRaisesRegex(ValueError, "pre-label journal"):
                verify(root)

    def test_rejects_relabelled_submission_elf(self):
        root, _ = self.outputs[0]
        path = root / "run.json"
        run = read(path)
        chosen = next(r for r in run["rows"] if r["role"] == "submission" and r["kind"] == "profile")
        reference = next(r for r in run["rows"] if r["case_id"] == chosen["case_id"] and r["role"] == "reference")
        chosen["elf_sha256"] = reference["elf_sha256"]
        with replace(path, json.dumps(run).encode()), self.assertRaisesRegex(ValueError, "wrong executable"):
            verify(root)

    def test_raw_profile_digest_is_bound_to_its_journal(self):
        root, _ = self.outputs[0]
        path = next((root / "runs").glob("*/records/*.jsonl"))
        with replace(path, path.read_bytes() + b"\n"), self.assertRaisesRegex(ValueError, "profile bytes differ"):
            verify(root)

    def test_rejects_unregistered_submission_process_in_trace(self):
        root, _ = self.outputs[0]
        path = root / "exec-trace"
        fake = b" worker [001] 1.00: sched_process_exec: filename=/tmp/rice_window pid=999999 old_pid=999999\n"
        with replace(path, path.read_bytes() + b"\n" + fake):
            with self.assertRaisesRegex(ValueError, "unregistered workload"):
                verify(root)


if __name__ == "__main__":
    unittest.main()
