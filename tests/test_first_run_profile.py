import copy
import hashlib
import json
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
ENTRY = REPO / "experiments/first_run_sampler/profile.py"
helper = runpy.run_path(str(ENTRY))
prior_select = runpy.run_path(str(REPO / "experiments/live_sampler_fast/profiles.py"))["select"]
ELF_HASH = hashlib.sha256(b"first ELF").hexdigest()
PID = 123


def receipt(counts):
    sites = []
    for off, n in sorted(counts.items()):
        periods, extra = divmod(n, 1024)
        pairs = periods + (extra >= 2)
        sites.append({"offset": off, "loads": n, "pairs": pairs, "near_pairs": 0,
                      "far_pairs": pairs, "sampled_address_span_bytes": 0})
    return {"schema": 2, "pid": PID, "threads": 1, "period": 1024, "line_bytes": 64, "sites": sites,
            **{k: sum(s[k] for s in sites) for k in ("loads", "pairs", "near_pairs", "far_pairs")}}


def capture(obj, *, arm="full", pid=PID):
    return helper["capture_for_digest"](ELF_HASH, b"calibration input\n", json.dumps(obj).encode(), pid, arm=arm)


class FirstRunProfile(unittest.TestCase):
    def test_exact_provenance_and_compatibility_with_registered_95_percent_rule(self):
        for counts in ({8: 90, 7: 5, 6: 5}, {100: 1}, {i: 7 for i in range(100)}, {4: 2049, 3: 1026, 1: 1025}):
            with self.subTest(count=len(counts)):
                obj = receipt(counts)
                profile = capture(obj)
                self.assertEqual(profile["elf_sha256"], ELF_HASH)
                self.assertEqual(profile["calibration"], {
                    "arm": "full", "pid": PID, "stdin_sha256": hashlib.sha256(b"calibration input\n").hexdigest(),
                    "receipt_sha256": hashlib.sha256(json.dumps(obj).encode()).hexdigest()})
                selected = prior_select(counts)
                self.assertEqual({k: profile[k] for k in selected}, selected)
                self.assertEqual(helper["resolve_for_digest"](profile, ELF_HASH), ",".join(map(str, selected["offsets"])))

    def test_wrong_capture_mode_pid_or_semantics_refused(self):
        obj = receipt({4: 1025, 8: 1})
        for arm in ("gate", "packed", "profiled", ""):
            with self.subTest(arm=arm), self.assertRaises(ValueError):
                capture(obj, arm=arm)
        with self.assertRaises(ValueError):
            capture(obj, pid=999)
        with self.assertRaises(ValueError):
            capture(receipt({}))
        for name, bad in (("period", True), ("near_pairs", 1), ("pid", 0), ("schema", 1)):
            with self.subTest(field=name), self.assertRaises(ValueError):
                capture({**obj, name: bad})
        for index, field, value in ((0, "pairs", 2), (0, "loads", 2**64), (1, "offset", 4),
                                    (1, "sampled_address_span_bytes", 3), (0, "offset", -1)):
            changed = copy.deepcopy(obj)
            changed["sites"][index][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                capture(changed)
        duplicate = json.dumps(obj).replace('"schema": 2', '"schema": 1, "schema": 2').encode()
        with self.assertRaises(ValueError):
            helper["capture_for_digest"](ELF_HASH, b"", duplicate, PID, arm="full")

    def test_resolution_refuses_different_elf_and_invalid_persisted_selection(self):
        profile = capture(receipt({4: 90, 8: 5, 9: 5}))
        with self.assertRaises(ValueError):
            helper["resolve_for_digest"](profile, "a" * 64)
        for field, bad in (("offsets", [4, 9]), ("selected_visits", 94), ("eligible_visits", 101),
                           ("offsets", [8, 4]), ("calibration", {**profile["calibration"], "arm": "profiled"}),
                           ("ranked_prefix", profile["ranked_prefix"][::-1])):
            with self.subTest(field=field), self.assertRaises(ValueError):
                helper["resolve_for_digest"]({**profile, field: bad}, ELF_HASH)

    def test_cli_uses_exact_paths_without_overwriting_completed_calibration(self):
        archive = REPO / "experiments/first_run_sampler/control/elf.tar.xz"
        import tarfile
        with tempfile.TemporaryDirectory() as temp, tarfile.open(archive, "r:xz") as ar:
            root = Path(temp)
            elf, stdin, raw, out = (root / s for s in ("program", "stdin", "receipt", "profile"))
            elf.write_bytes(ar.extractfile("bin/path_switch").read())
            stdin.write_bytes(b"A 2097573\n")
            raw.write_text(json.dumps(receipt({5894: 2097573})))
            cmd = [sys.executable, "-B", ENTRY, "capture", "--elf", elf, "--stdin", stdin, "--receipt", raw,
                   "--pid", str(PID), "--arm", "full", "--out", out]
            subprocess.run(cmd, check=True, capture_output=True)
            original = out.read_bytes()
            self.assertNotEqual(subprocess.run(cmd, capture_output=True).returncode, 0)
            self.assertEqual(out.read_bytes(), original)
            resolve = [sys.executable, "-B", ENTRY, "resolve", "--elf", elf, "--profile", out]
            self.assertEqual(subprocess.check_output(resolve), b"5894\n")
            with elf.open("ab") as stream:
                stream.write(b"changed")
            self.assertNotEqual(subprocess.run(resolve, capture_output=True).returncode, 0)


if __name__ == "__main__":
    unittest.main()
