"""The offline branch/sort replay must reject mismatched or partial evidence."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


EXPERIMENTS = Path(__file__).resolve().parents[1] / "experiments"
with patch.object(sys, "path", [str(EXPERIMENTS), *sys.path]):
    import reference_features as replay


def sha(data):
    return hashlib.sha256(data).hexdigest()


class ReferenceFeaturesTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.dataset = {
            "schema": 1, "complete": True, "smoke": False, "failures": [],
            "corpus_id": "cpp-reference-v1", "source_language": "CPP20",
            "cpuid_profile": "x86-64-v2", "latency_bound_version": 1,
            "runtime_image": "fixture", "repetitions": 3, "samples": [],
        }
        self.case_dir = self.root / "branch"
        self.case_dir.mkdir()
        for name, contents in (("Main", b"ELF fixture"), ("Main.cpp", b"source"), ("1.stdin", b"1\n")):
            (self.case_dir / name).write_bytes(contents)
        # Each native measurement brackets the same metered execution. Sorted
        # together, their middle values are 61 and 64 (rounded median = 63).
        accepted = {"status": "Accepted", "exit_code": 0, "verdict_source": "stdout"}
        for i, (before, after) in enumerate([(100, 64), (22, 83), (61, 5)], start=1):
            self.dataset["samples"].append({
                "workload_id": "branch", "scale": 1, "split": "validation", "repetition": i,
                "binary_sha256": sha(b"ELF fixture"), "source_sha256": sha(b"source"),
                "input_sha256": sha(b"1\n"), "expected_sha256": sha(b"567\n"),
                "native_before": {**accepted, "cpu_time_ns": before},
                "native_after": {**accepted, "cpu_time_ns": after},
                "metered": {"exit_code": 0, "work_measurement_execution": {
                    "state": "complete", "output_matches": True},
                    "work_measurement": {"complete": True, "quality": "uninterrupted",
                        "family": "cpp", "cpuid_profile": "x86-64-v2", "images": 1, "processes": 1,
                        "instructions": 12, "rep_bytes": i,
                        "latency_bound": {"version": 1, "cycles_x16": 49}}},
            })

    def test_reference_preserves_all_native_and_metered_repetitions(self):
        binary, stdin, row = replay.reference_case(self.dataset, self.root, "branch", 1)
        self.assertEqual(binary, self.case_dir / "Main")
        self.assertEqual(stdin, b"1\n")
        self.assertEqual(row["native_cpu_ns"], [5, 22, 61, 64, 83, 100])
        self.assertEqual(row["native_median_ns"], 63)
        self.assertEqual(row["baseline"], [
            {"instructions": 12, "latency_x16": 49, "rep_bytes": i} for i in (1, 2, 3)])
        self.assertEqual(row["expected_sha256"], sha(b"567\n"))

    def test_rejects_archives_with_different_reference_provenance(self):
        data = self.root / "archive.json"
        args = SimpleNamespace(dataset=data)
        for key, value in [("schema", 2), ("complete", False), ("smoke", True),
                           ("corpus_id", "cpp-reference-v2"), ("source_language", "C17"),
                           ("cpuid_profile", "host"), ("latency_bound_version", 2)]:
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "dataset"):
                data.write_text(json.dumps({**self.dataset, key: value}))
                args.dataset_sha256 = sha(data.read_bytes())
                replay.collect(args)
        data.write_text(json.dumps(self.dataset))
        args.dataset_sha256 = replay.PR121_DATASET_SHA256
        with self.assertRaisesRegex(ValueError, "dataset SHA-256 mismatch"):
            replay.collect(args)

    def test_archive_rejects_different_artifact_bytes(self):
        for name in ("Main", "Main.cpp", "1.stdin"):
            with self.subTest(name=name):
                path = self.case_dir / name
                original = path.read_bytes()
                path.write_bytes(original + b"!")
                with self.assertRaises(ValueError):
                    replay.reference_case(self.dataset, self.root, "branch", 1)
                path.write_bytes(original)

    def test_archive_rejects_unpaired_or_unqualified_repetitions(self):
        for path, bad in [
            (("repetition",), 1), (("split",), "training"), (("expected_sha256",), sha(b"wrong")),
            (("native_after", "status"), "Runtime Error"),
            (("native_before", "cpu_time_sampling"), True),
            (("native_after", "cpu_time_normalization"), True), (("native_after", "cpu_time_ns"), 0),
            (("metered", "work_measurement_execution", "output_matches"), False),
            (("metered", "work_measurement", "cpuid_profile"), "host"),
            (("metered", "work_measurement", "quality"), "interrupted"),
            (("metered", "work_measurement", "latency_bound", "cycles_x16"), 47),
        ]:
            with self.subTest(path=path):
                dataset = copy.deepcopy(self.dataset)
                obj = dataset["samples"][1]
                for key in path[:-1]:
                    obj = obj[key]
                obj[path[-1]] = bad
                with self.assertRaises(ValueError):
                    replay.reference_case(dataset, self.root, "branch", 1)
        self.dataset["samples"].pop()
        with self.assertRaises(ValueError):
            replay.reference_case(self.dataset, self.root, "branch", 1)

    def test_comparison_checks_the_whole_range_at_the_exact_rational_boundary(self):
        # ceil/floor or float rounding would lose the one-count distinction.
        n = 2**54
        for b, s, expected in [
            ([3*n, 3*n], [4*n, 4*n], "spans"),
            ([3*n, 3*n], [4*n-1, 4*n-1], "insufficient"),
            ([3*n, 3*n], [4*n-1, 4*n], "overlap"),
            ([3*n, 3*n+1], [4*n, 4*n], "overlap"),
            ([0, 0], [0, 0], "insufficient"), ([0, 0], [1, 2], "spans"),
        ]:
            with self.subTest(b=b, s=s):
                branch = {"native_median_ns": 1, "observations": [{"counter": v} for v in b]}
                sort = {"native_median_ns": 2, "observations": [{"counter": v} for v in s]}
                comparison = replay.compare(branch, sort)
                self.assertEqual(comparison["required_sort_to_branch_ratio"], "4/3")
                self.assertEqual(comparison["features"]["counter"], {
                    "branch_range": [min(b), max(b)], "sort_range": [min(s), max(s)],
                    "result": expected,
                })

    def test_observation_rejects_impossible_and_malformed_counters(self):
        raw = {"bpred": True, "cbr_executed": 1, "cond_branches": 1, "mispredicts": 1,
               "loads": 8, "load_pattern": {"version": 1, "first": 2, "same_line": 1,
                                           "adjacent_line": 2, "nonadjacent_line": 3}}
        self.assertEqual(replay.observation(raw), {
            "gshare_mispredicts": 1, "conditional_branches": 1, "load_instructions": 8,
            "first": 2, "same_line": 1, "adjacent_line": 2, "nonadjacent_line": 3,
        })
        for key, bad in [("bpred", 1), ("cond_branches", 2), ("cond_branches", True),
                         ("mispredicts", 2), ("loads", 7), ("loads", 2**64)]:
            with self.subTest(key=key, bad=bad), self.assertRaises(ValueError):
                replay.observation({**raw, key: bad})
        for key, bad in [("version", True), ("version", 2), ("same_line", -1),
                         ("first", False), ("nonadjacent_line", 2**64), ("extra", 0)]:
            with self.subTest(key=key, bad=bad), self.assertRaises(ValueError):
                replay.observation({**raw, "load_pattern": {**raw["load_pattern"], key: bad}})
        missing = dict(raw["load_pattern"])
        del missing["adjacent_line"]
        with self.assertRaises((KeyError, ValueError)):
            replay.observation({**raw, "load_pattern": missing})

    def test_controls_require_both_effects_on_all_repeats(self):
        controls = {}
        for mode in range(4):
            permuted = mode & 1
            row = {"gshare_mispredicts": 20 if mode & 2 else 2, "first": 2,
                   "same_line": 0 if permuted else 30720, "adjacent_line": 0 if permuted else 2047,
                   "nonadjacent_line": 32767 if permuted else 0}
            controls[str(mode)] = {"observations": [row.copy(), row.copy()]}
        replay.check_controls(controls)
        for mode, key, value in [(2, "gshare_mispredicts", 2), (3, "gshare_mispredicts", 1),
                                 (1, "nonadjacent_line", 32766), (3, "nonadjacent_line", 0),
                                 (0, "same_line", 30721), (2, "adjacent_line", 2048),
                                 (1, "first", 1)]:
            altered = copy.deepcopy(controls)
            altered[str(mode)]["observations"][1][key] = value
            with self.subTest(mode=mode, key=key), self.assertRaises(ValueError):
                replay.check_controls(altered)

    def test_process_requires_an_exact_stdout_oracle_and_successful_exit(self):
        command = [sys.executable, "-c",
                   "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read()); sys.stderr.write('detail')"]
        self.assertEqual(replay.run_checked(command, b"42\n", sha(b"42\n"), 5), b"detail")
        with self.assertRaisesRegex(ValueError, "stdout"):
            replay.run_checked(command, b"42\n", sha(b"42"), 5)
        with self.assertRaisesRegex(ValueError, "exited 4"):
            replay.run_checked([sys.executable, "-c", "raise SystemExit(4)"], b"", sha(b""), 5)

    def test_failed_replay_checkpoints_and_preserves_existing_output(self):
        data = self.root / "archive.json"
        data.write_text(json.dumps(self.dataset))
        for name in ["client.so", "control", "bin64/drrun", "lib64/release/libdynamorio.so",
                     *[f"ext/lib64/release/lib{name}.so" for name in ("drmgr", "drreg", "drutil")]]:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(name.encode())
        output = self.root / "result"
        args = SimpleNamespace(dataset=data, dataset_sha256=sha(data.read_bytes()),
                               fixtures=self.root, sdk=self.root,
                               client=self.root / "client.so", control=self.root / "control",
                               output=output, cpu=0, repeats=2, timeout=5, cachesim_root=None)
        with patch.object(replay.os, "sched_getaffinity", return_value={0}), \
             patch.object(replay.os, "sched_setaffinity"), \
             patch.object(replay, "run_checked", side_effect=ValueError("bad output oracle")) as run:
            with self.assertRaisesRegex(ValueError, "bad output oracle"):
                replay.collect(args)
            saved = (output / "results.json").read_bytes()
            receipt = json.loads(saved)
            self.assertIs(receipt["complete"], False)
            self.assertEqual(receipt["failure"], "bad output oracle")
            self.assertEqual(receipt["dataset_sha256"], sha(data.read_bytes()))
            for ext in ("drmgr", "drreg", "drutil"):
                self.assertEqual(receipt["tools_sha256"][ext], sha(f"ext/lib64/release/lib{ext}.so".encode()))
            self.assertEqual(receipt["controls"]["0"]["observations"], [])
            self.assertNotIn("comparisons", receipt)
            with self.assertRaises(FileExistsError):
                replay.collect(args)
            run.assert_called_once()
            self.assertEqual((output / "results.json").read_bytes(), saved)


if __name__ == "__main__":
    unittest.main()
