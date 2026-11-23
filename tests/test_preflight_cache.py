from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
from threading import Event
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments"))
from preflight_cache import session


class CacheProtocol(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.cache = self.root / "cache"
        self.elf = self.root / "elf"
        header = bytearray(120)
        header[:7] = b"\x7fELF\x02\x01\x01"
        struct.pack_into("<HH", header, 16, 2, 62)
        struct.pack_into("<Q", header, 32, 64)
        struct.pack_into("<HH", header, 54, 56, 1)
        struct.pack_into("<I", header, 64, 1)
        self.elf.write_bytes(header)
        self.stdin = self.root / "stdin"
        self.stdin.write_bytes(b"one")
        self.before = os.sched_getaffinity(0)
        os.sched_setaffinity(0, {min(self.before)})
        self.addCleanup(os.sched_setaffinity, 0, self.before)
        self.capture = self.enterContext(patch.object(session.combined, "capture", side_effect=self.fake_capture))
        self.enterContext(patch.object(session.select_policy, "resolve", side_effect=self.fake_select))
        self.verify_tools = self.enterContext(patch.object(session.combined, "verify_tools"))

    @staticmethod
    def fake_capture(payload, probe, elf, stdin, draft):
        draft.mkdir()
        (draft / "full").mkdir()
        (draft / "full/stdin").write_bytes(stdin.read_bytes())
        session.anchor.save_new(draft / "combined.json", {
            "elf_sha256": session.elf_digest(elf), "stdin_sha256": session.elf_digest(stdin)})

    @staticmethod
    def fake_select(elf, draft, refs, probe_sha, method):
        selected = refs[0] if refs else draft
        if not refs:
            session.anchor.save_new(draft / "policy.json", {"stdin": session.elf_digest(draft / "full/stdin")})
        return dict(action="reuse" if refs else "calibrate", reference_index=0 if refs else None,
                    policy_sha256=session.elf_digest(selected / "policy.json"))

    def prepare(self, refs=(), method="footprint"):
        return session.prepare(self.root, self.root / "probe", self.elf, self.stdin, self.cache, refs, method)

    def test_model_dependency_changes_namespace(self):
        other = self.root / "src"
        for path in session.SOURCE_FILES:
            dest = other / path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"original")
        original = session.implementation_hash(other)
        (other / "guarded_native/transfer.py").write_bytes(b"changed scaling")
        self.assertNotEqual(original, session.implementation_hash(other))

    def test_invalid_deadline_spends_no_preflight(self):
        result = session.enforce(self.root, self.root / "probe", self.elf, self.stdin, self.cache,
                                 [], "footprint", self.root / "out", 0, 120000, 5)
        self.assertEqual(result["status"], "unavailable")
        self.assertFalse(self.cache.exists())
        self.capture.assert_not_called()

    def test_exact_input_method_and_session_own_hits(self):
        cold = self.prepare()
        warm = self.prepare()
        self.assertFalse(cold["hit"])
        self.assertEqual(warm, {**cold, "hit": True})
        self.verify_tools.assert_called_once()
        self.verify_tools.side_effect = ValueError("changed runtime")
        with self.assertRaisesRegex(ValueError, "changed runtime"):
            self.prepare()
        self.verify_tools.side_effect = None
        self.assertNotEqual(cold["key"], self.prepare(method="temporal")["key"])
        self.stdin.write_bytes(b"changed")
        self.assertNotEqual(cold["key"], self.prepare()["key"])
        self.cache = self.root / "other-session"
        with self.assertRaisesRegex(ValueError, "this session"):
            self.prepare([cold["capture"]])

    def test_placement_and_invocation_must_match_paid_bank(self):
        origin = self.prepare()
        with patch.dict(os.environ, {"DURANTE_CACHE_CONTEXT_TEST": "changed"}):
            self.assertNotEqual(origin["key"], self.prepare()["key"])
            with self.assertRaisesRegex(ValueError, "from this context"):
                self.prepare([origin["capture"]])
        if len(self.before) > 1:
            os.sched_setaffinity(0, self.before)
            with self.assertRaisesRegex(ValueError, "one CPU"):
                self.prepare()
            other, = sorted(self.before)[1:2]
            os.sched_setaffinity(0, {other})
            with self.assertRaisesRegex(ValueError, "from this context"):
                self.prepare([origin["capture"]])

    def test_reference_order_and_changed_reference_evidence(self):
        first = self.prepare()["capture"]
        self.stdin.write_bytes(b"two")
        second = self.prepare()["capture"]
        self.stdin.write_bytes(b"three")
        ab = self.prepare([first, second])
        ba = self.prepare([second, first])
        self.assertNotEqual(ab["key"], ba["key"])
        self.assertNotEqual(ab["policy"].read_bytes(), ba["policy"].read_bytes())
        self.assertTrue(self.prepare([first, second])["hit"])
        with self.assertRaisesRegex(ValueError, "distinct paid"):
            self.prepare([first, first])
        with self.assertRaisesRegex(ValueError, "paid calibration"):
            self.prepare([ab["capture"]])
        (second / "policy.json").write_bytes(b"replaced")
        with self.assertRaisesRegex(ValueError, "evidence changed"):
            self.prepare([first, second])

    def test_corrupt_artifact_prevents_launch_and_cache_repair(self):
        cold = self.prepare()
        (cold["capture"] / "full/stdin").write_bytes(b"unrelated")
        with patch.object(session.run, "run", side_effect=AssertionError("launched from damaged entry")):
            result = session.enforce(self.root, self.root / "probe", self.elf, self.stdin, self.cache,
                                     [], "footprint", self.root / "out", 1, 120000, 5)
        self.assertEqual((result["status"], result["pid"]), ("unavailable", None))
        self.assertEqual(self.capture.call_count, 1)

    def test_interrupted_preflight_does_not_publish(self):
        def fail_after_capture(*args):
            self.fake_capture(*args)
            raise ValueError("interrupted")
        self.capture.side_effect = fail_after_capture
        with self.assertRaisesRegex(ValueError, "interrupted"):
            self.prepare()
        self.capture.side_effect = self.fake_capture
        self.assertFalse(self.prepare()["hit"])
        self.assertTrue(self.prepare()["hit"])
        self.assertEqual(self.capture.call_count, 2)

    def test_concurrent_first_use_publishes_one_capture(self):
        started, release = Event(), Event()
        def wait_for_competitor(*args):
            started.set()
            self.assertTrue(release.wait(10))
            self.fake_capture(*args)
        self.capture.side_effect = wait_for_competitor
        with ThreadPoolExecutor(max_workers=2) as threads:
            first = threads.submit(self.prepare)
            self.assertTrue(started.wait(10))
            second = threads.submit(self.prepare)
            release.set()
            cold, warm = first.result(10), second.result(10)
        self.assertEqual(self.capture.call_count, 1)
        self.assertEqual(warm, {**cold, "hit": True})
        self.assertFalse(cold["hit"])


class LiveCache(unittest.TestCase):
    @unittest.skipUnless(all(os.environ.get(k) for k in ("DURANTE_SHAPE_RUNTIME", "DURANTE_SHAPE_BINARIES",
                                                        "DURANTE_TEMPORAL_PROBE")), "set the registered runtime, ELFs and probe")
    def test_simultaneous_cli_launches_fresh_enforcement(self):
        runtime = Path(os.environ["DURANTE_SHAPE_RUNTIME"])
        elf = Path(os.environ["DURANTE_SHAPE_BINARIES"]) / "fenwick"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stdin = root / "stdin"
            stdin.write_text("16384 3000000\n")
            results = []
            with ExitStack() as stack:
                processes = []
                trials = ((1, "budget_exceeded", 123), (3000, "within_budget", 0))
                for index, (ms, _, _) in enumerate(trials):
                    out = root / str(index)
                    cmd = ["taskset", "-c", str(min(os.sched_getaffinity(0))), sys.executable, "-B", session.__file__,
                           "--payload", runtime, "--probe", os.environ["DURANTE_TEMPORAL_PROBE"], "--elf", elf,
                           "--stdin", stdin, "--cache", root / "cache", "--out", out, "--limit-ms", str(ms)]
                    child = stack.enter_context(subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE))
                    processes.append(child)
                for index, (child, (_, status, code)) in enumerate(zip(processes, trials)):
                    stdout, stderr = child.communicate(timeout=35)
                    self.assertFalse(stderr, stderr)
                    result = json.loads(stdout)
                    self.assertEqual((child.returncode, result["status"]), (code, status), result)
                    usage = json.loads((root / str(index) / "cache-use.json").read_bytes())
                    results.append((usage, result))
            (first, a), (second, b) = results
            self.assertEqual(sorted((first["hit"], second["hit"])), [False, True])
            self.assertEqual(first["key"], second["key"])
            self.assertEqual(a["selection_sha256"], b["selection_sha256"])
            self.assertNotEqual(a["pid"], b["pid"])


if __name__ == "__main__":
    unittest.main()
