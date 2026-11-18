import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments"))
import collect_branch_model as study


class BranchCollectionTests(unittest.TestCase):
    def setUp(self):
        self.raw = {"bpred": True, "instructions": 100, "loads": 20, "cond_branches": 12,
                    "cbr_executed": 12, "mispredicts": 6}
        self.receipt = {"instructions": 100, "rep_bytes": 7, "authority": "diagnostic_only",
                        "integrity_verified": False, "measurement_contract": {
                            "counter_integrity": "application_mutable", "trusted_verdict_eligible": False}}

    def test_counts_require_matching_same_run_and_valid_branch_count(self):
        self.assertEqual(study.branch_counts(self.raw, self.receipt),
                         {"load_instructions": 20, "gshare_mispredicts": 6, "conditional_branches": 12})
        for change in ({"bpred": False}, {"load_pattern": {}}, {"instructions": 101}, {"loads": 101},
                       {"cbr_executed": 11}, {"mispredicts": 13}, {"mispredicts": True}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                study.branch_counts({**self.raw, **change}, self.receipt)

    def test_paired_meter_runs_share_exec_prefix_and_keep_production_counters(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            program = root / "Main"
            stdin = root / "1.stdin"
            prefix = ("env", "DYNAMORIO_CONFIGDIR=" + tmp, "setarch", "-R")
            for enabled in (False, True):
                folder = root / ("probe" if enabled else "meter")
                folder.mkdir()
                report = folder / "features.json"
                if enabled:
                    report.write_text(json.dumps(self.raw))
                with patch.object(study, "run_with_meter", return_value=(123, 456, self.receipt)) as run:
                    cpu, counters = study.measure({}, program, stdin, b"ok\n", folder, enabled, 10, prefix=prefix)
                opts = f'-out "{report}" -bpred' if enabled else None
                run.assert_called_once_with({}, program, stdin, b"ok\n", folder, opts, 10, prefix=prefix)
                self.assertEqual(cpu, 456)
                self.assertEqual((counters["instructions"], counters["rep_bytes"]), (100, 7))
                self.assertEqual(counters.get("gshare_mispredicts"), 6 if enabled else None)

    def test_manifest_binds_original_seven_and_both_new_families(self):
        corpus = study.load_corpus()
        self.assertEqual(set(corpus["families"]), {"integer", "rep_copy", "sort", "branch", "rep_fill",
                                                   "lower_bound", "window_sum", "merge_streams", "hash_probe"})
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "extra.json"
            data = json.loads(study.BRANCH_CORPUS.read_bytes())
            data["base_corpus_sha256"] = "0" * 64
            manifest.write_text(json.dumps(data))
            with patch.object(study, "BRANCH_CORPUS", manifest), self.assertRaisesRegex(ValueError, "base"):
                study.load_corpus()


if __name__ == "__main__":
    unittest.main()
