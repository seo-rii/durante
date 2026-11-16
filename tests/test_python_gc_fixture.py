"""Observe native Python GC and reject absent or incomplete collection evidence."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from runtime_workload_fixtures import workload as fixture


FIXTURE = Path(fixture.__file__).resolve()
OUTPUTS = {"threads": "threads:150015000\n",
           "allocation-gc": "allocation-gc:522240:retained:32640:observed\n",
           "processes": "processes:204\n"}


# Wrap the real API in a fresh interpreter so the test observes real collection
# results without sharing disabled GC state with unittest. Keep the report out
# of the fixture's exact stdout/stderr contract.
NATIVE_GC_PROBE = r"""
import gc
import json
from pathlib import Path
import runpy
import sys

fixture = runpy.run_path(sys.argv[1])
report = {"runtime": sys.implementation.name, "disabled": True, "results": []}
if sys.implementation.name == "pypy":
    collect_step = gc.collect_step
    def observe():
        result = collect_step()
        report["disabled"] &= not gc.isenabled()
        report["results"].append(result.major_is_done)
        return result
    gc.collect_step = observe
else:
    collect = gc.collect
    def observe():
        result = collect()
        report["disabled"] &= not gc.isenabled()
        report["results"].append(result)
        return result
    gc.collect = observe
fixture["allocation_gc"]()
Path(sys.argv[2]).write_text(json.dumps(report))
"""


class PythonGCFixtureTests(unittest.TestCase):
    def run_allocation(self, runtime, gc_api):
        self.output = io.StringIO()
        implementation = SimpleNamespace(name=runtime)
        with patch.object(fixture, "gc", gc_api), \
                patch.object(fixture, "sys", SimpleNamespace(implementation=implementation)), \
                redirect_stdout(self.output):
            fixture.allocation_gc()
        return self.output.getvalue()

    def gc_api(self):
        return SimpleNamespace(
            disable=Mock(),
            collect=Mock(side_effect=AssertionError("unexpected CPython GC API")),
            collect_step=Mock(side_effect=AssertionError("unexpected PyPy GC API")))

    def test_native_workloads_keep_their_exact_outputs(self):
        for mode, expected in OUTPUTS.items():
            with self.subTest(mode=mode):
                result = subprocess.run([sys.executable, "-I", "-S", str(FIXTURE), mode],
                                        capture_output=True, text=True, timeout=20, check=False)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, expected)
                self.assertEqual(result.stderr, "")

    def test_native_gc_returns_real_collection_evidence(self):
        with tempfile.TemporaryDirectory(prefix="durante-python-gc-unit-") as temporary:
            report_path = Path(temporary) / "gc.json"
            result = subprocess.run([sys.executable, "-I", "-S", "-c", NATIVE_GC_PROBE,
                                     str(FIXTURE), str(report_path)],
                                    capture_output=True, text=True, timeout=20, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, OUTPUTS["allocation-gc"])
            self.assertEqual(result.stderr, "")
            report = json.loads(report_path.read_text())
        self.assertEqual(report["runtime"], sys.implementation.name)
        self.assertTrue(report["disabled"])
        results = report["results"]
        if report["runtime"] == "pypy":
            self.assertTrue(all(type(value) is bool for value in results))
            self.assertEqual(sum(results), 4)
            self.assertGreater(len(results), 4)  # Intermediate steps alone are insufficient.
            self.assertLessEqual(len(results), 4000)
        else:
            self.assertEqual(len(results), 4)
            self.assertTrue(all(type(value) is int for value in results))
            self.assertGreater(sum(results), 0)

    def test_cpython_requires_positive_reclaimed_object_count(self):
        for value in (0, -1):
            with self.subTest(value=value):
                api = self.gc_api()
                api.collect = Mock(return_value=value)
                with self.assertRaisesRegex(RuntimeError, "no cyclic collection observed"):
                    self.run_allocation("cpython", api)
                self.assertEqual(self.output.getvalue(), "")
                self.assertEqual(api.collect.call_count, 4)
                api.collect_step.assert_not_called()

    def test_pypy_waits_for_major_completion_at_each_collection(self):
        api = self.gc_api()
        api.collect_step = Mock(side_effect=[
            SimpleNamespace(major_is_done=done, count=50, num_major_collects=99)
            for done in (False, False, True) * 4])
        self.assertEqual(self.run_allocation("pypy", api), OUTPUTS["allocation-gc"])
        self.assertEqual(api.collect_step.call_count, 12)
        api.disable.assert_called_once_with()
        api.collect.assert_not_called()

    def test_pypy_rejects_intermediate_steps_even_after_an_earlier_major_collection(self):
        for completed in (0, 1, 3):
            with self.subTest(previous_major_collections=completed):
                api = self.gc_api()
                results = iter([SimpleNamespace(major_is_done=True)] * completed)
                incomplete = SimpleNamespace(major_is_done=False, count=50, num_major_collects=99)
                api.collect_step = Mock(side_effect=lambda: next(results, incomplete))
                with self.assertRaisesRegex(RuntimeError, "major collection did not finish within 1000 steps"):
                    self.run_allocation("pypy", api)
                self.assertEqual(self.output.getvalue(), "")
                self.assertEqual(api.collect_step.call_count, completed + 1000)
                api.collect.assert_not_called()

    def test_gc_api_exceptions_are_propagated_without_fallback(self):
        for runtime, method in (("cpython", "collect"), ("pypy", "collect_step")):
            with self.subTest(runtime=runtime):
                api = self.gc_api()
                error = ValueError("injected GC failure")
                setattr(api, method, Mock(side_effect=error))
                with self.assertRaises(ValueError) as raised:
                    self.run_allocation(runtime, api)
                self.assertIs(raised.exception, error)
                self.assertEqual(self.output.getvalue(), "")
                getattr(api, method).assert_called_once_with()

    def test_pypy_rejects_unrelated_stats_without_major_completion_field(self):
        api = self.gc_api()
        api.collect_step = Mock(return_value=SimpleNamespace(count=50, num_major_collects=99))
        with self.assertRaises(AttributeError):
            self.run_allocation("pypy", api)
        self.assertEqual(self.output.getvalue(), "")
        api.collect.assert_not_called()

    def test_successful_gc_evidence_does_not_hide_corrupted_retained_allocations(self):
        for runtime in ("cpython", "pypy"):
            with self.subTest(runtime=runtime):
                blocks = []

                def allocate(size):
                    block = bytearray(size)
                    blocks.append(block)
                    return block

                def collect():
                    if len(blocks) == 16 * 128:
                        blocks[-1][0] ^= 1
                    return SimpleNamespace(major_is_done=True) if runtime == "pypy" else 1

                api = self.gc_api()
                setattr(api, "collect_step" if runtime == "pypy" else "collect", Mock(side_effect=collect))
                with patch.object(fixture, "bytearray", allocate, create=True), \
                        self.assertRaisesRegex(RuntimeError, "retained allocation changed after collection"):
                    self.run_allocation(runtime, api)
                self.assertEqual(self.output.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
