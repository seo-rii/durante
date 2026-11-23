import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments"))
from preflight_cache.verify import Accounting, verify
from shape_calibration.verify_combined import unpack

DATA = ROOT / "experiments/results/gce-preflight-cache"


class Timing(unittest.TestCase):
    def test_controller_and_waited_child_costs_are_separate(self):
        before = dict(wall_ns=1, self_user_us=10, self_sys_us=10, children_user_us=20, children_sys_us=20)
        after = dict(wall_ns=123456, self_user_us=13, self_sys_us=12, children_user_us=27, children_sys_us=26)
        clock = Accounting()
        self.assertEqual(clock.duration(dict(before=before, after=after)),
                         dict(wall_ns=123455, self_cpu_ns=5000, children_cpu_ns=13000, cpu_ns=18000))
        with self.assertRaisesRegex(ValueError, "overlapping time readings"):
            clock.duration(dict(before=before, after=after))


class ArchivedCache(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = unpack(DATA / "output.tar.xz", Path(cls.temp.name))

    def test_replays_archived_metrics(self):
        self.assertEqual(verify(self.root), json.loads((DATA / "metrics.json").read_bytes()))

    def test_refuses_invented_warm_hit(self):
        path = self.root / "summary.json"
        old = path.read_bytes()
        try:
            summary = json.loads(old)
            row = next(r for r in summary["rows"] if r["arm"] == "session")
            row["prepared"]["hit"] = True
            path.write_text(json.dumps(summary))
            with self.assertRaisesRegex(ValueError, "session history"):
                verify(self.root)
        finally:
            path.write_bytes(old)

    def test_refuses_unrelated_source_fingerprint(self):
        collection = json.loads((DATA / "collection.json").read_bytes())
        collection["input_sources_sha256"]["experiments/preflight_cache/session.py"] = "0" * 64
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "collection.json"
            path.write_text(json.dumps(collection))
            with self.assertRaisesRegex(ValueError, "guest implementation"):
                verify(self.root, path)

    def test_refuses_uncharged_target(self):
        path = self.root / "exec-trace"
        before = path.read_bytes()
        try:
            with path.open("ab") as f:
                f.write(b" test [001] 1.000: sched_process_exec: filename=/bin/drrun pid=4194303 old_pid=4194303\n")
            with self.assertRaisesRegex(ValueError, "unaccounted"):
                verify(self.root)
        finally:
            path.write_bytes(before)


if __name__ == "__main__":
    unittest.main()
