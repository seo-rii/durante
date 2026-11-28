import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import contest_corpus as corpus


def budget_row(mode, requested, reported, instructions):
    half = mode == "budget-half"
    return {
        "errors": [], "warnings": [], "harness_killed": False,
        "output_ok": True, "stderr": "", "exit": 123 if half else 0, "budget": requested,
        "report": {
            "execution": {"cleanup_complete": True, "records_cleanup_complete": True,
                          "state": "work_budget_exceeded" if half else "complete", "exit_code": 0},
            "measurement": {"complete": not half, "valid": not half,
                            "instructions": None if half else instructions,
                            "rep_breakdown": {"movs_bytes": 0, "stos_bytes": 0}},
            "work_budget": {"limit": reported, "instructions": instructions, "movs_bytes": 0, "stos_bytes": 0,
                            "status": "exceeded" if half else "within_budget",
                            "observation": "live" if half else "terminal"},
        },
    }


class ContestCorpusTest(unittest.TestCase):
    def test_java_profiles_use_distinct_class_space_flags(self):
        self.assertIn("java", corpus.DEFAULT_LANGS)
        self.assertNotIn("java-cds", corpus.DEFAULT_LANGS)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            expected = ["java", "-XX:ReservedCodeCacheSize=64m", "-XX:-UseCompressedClassPointers",
                        "-Xmx256m", "-Xss16384k", "-XX:MaxDirectMemorySize=16m", "-XX:MaxMetaspaceSize=64m",
                        "-Dfile.encoding=UTF-8", "-XX:+UseSerialGC", "-DONLINE_JUDGE=1"]
            for lang in ("java", "java-cds"):
                with self.subTest(lang=lang), patch.object(corpus, "compile_step"):
                    out = root / lang
                    out.mkdir()
                    command = corpus.LANGS[lang].build("bfs_grid", Path("Main.java"), out, [])
                    flags = expected.copy()
                    if lang == "java-cds":
                        flags[2] = "-XX:CompressedClassSpaceSize=64m"
                    self.assertEqual(command, [*flags, "-cp", str(out / "classes"), "Main"])

    def test_budget_receipt_matches_requested_limit_and_observed_work(self):
        for mode, work, reported, accepted in (
            ("budget-half", 101, 100, True), ("budget-half", 101, 7, False),
            ("budget-half", 100, 100, False),
            ("budget-large", 100, 100, True), ("budget-large", 100, 900, False),
            ("budget-large", 101, 100, False),
        ):
            with self.subTest(mode=mode, work=work, reported=reported):
                row = budget_row(mode, 100, reported, work)
                corpus.check(row, mode)
                self.assertEqual(not row["errors"], accepted, row["errors"])

    def test_missing_selected_submission_is_recorded_when_no_cell_can_run(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            out = root / "results"
            with patch.object(corpus, "CONTEST", root), patch.object(corpus, "prepare_inputs", return_value={}), \
                    contextlib.redirect_stdout(io.StringIO()):
                status = corpus.main(["--native-only", "--sdk", temp, "--client", temp, "--runner-bin", temp,
                                      "--output-dir", str(out), "--lang", "python", "--problem", "lcs"])
            self.assertEqual(status, 1)
            result = json.loads((out / "results.json").read_text())
            self.assertEqual(result["summaries"], [])
            failure, = result["build_failures"]
            self.assertEqual((failure["problem"], failure["lang"]), ("lcs", "python"))
            self.assertIn("sol.py", failure["error"])


if __name__ == "__main__":
    unittest.main()
