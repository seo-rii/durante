import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments"))
import collect_load_model as collector


class LoadCollectionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="load pairs ")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.program = self.root / "Main"
        self.stdin = self.root / "input"
        self.stdin.write_bytes(b"fixture\n")
        self.tools = {name: self.root / name for name in ("drrun", "meter", "probe")}
        self.pid = 2468
        self.receipt = {
            "complete": True, "valid": True, "cpuid_profile": "x86-64-v2",
            "quality": "uninterrupted", "image_count": 1, "process_count": 1,
            "processes": [{"exit_code": 0}], "segments": [{"threads": 1}],
            "rep_bytes_enabled": True, "unsupported_rep_entries": 0,
            "instructions": 64, "rep_bytes": 7,
            "latency_bound": {"version": 1, "cycles_x16": 256},
        }
        self.loads = {"schema": 1, "pid": self.pid, "threads": 1,
                      "instructions": 64, "load_instructions": 9}

    def measure(self, label, enabled=True, produce_probe=True):
        folder = self.root / label
        captured = []

        def run(command, stdin, expected, timeout):
            captured.extend(command)
            self.assertEqual((stdin, expected, timeout), (self.stdin, b"ok", 5))
            if enabled and produce_probe:
                (folder / "loads.json").write_text(json.dumps(self.loads))
            return self.pid, 12345

        with patch.object(collector, "checked_run", side_effect=run), \
             patch.object(collector, "aggregate_records", return_value=self.receipt) as aggregate:
            result = collector.metered(self.tools, self.program, self.stdin, b"ok", folder, enabled, 5)
            aggregate.assert_called_once_with(folder / "records", self.pid)
        return captured, result

    def test_matched_options_change_only_the_extra_client(self):
        for enabled in (False, True):
            with self.subTest(probe=enabled):
                label = "on" if enabled else "off"
                cmd, (cpu, counters) = self.measure(label, enabled)
                folder = self.root / label
                opts = f'-outdir "{folder}/records" -rep_bytes -cpuid_profile x86-64-v2 -latency_bound'
                expected = [self.tools["drrun"], "-quiet", "-client", self.tools["meter"], "00000000", opts]
                if enabled:
                    expected += ["-client", self.tools["probe"], "00000001", f'-out "{folder}/loads.json"']
                expected += ["-follow_children", "-disable_traces", "-vm_size", "32M",
                             "-vmheap_size", "32M", "--", self.program]
                self.assertEqual(cmd, expected)
                self.assertEqual((cpu, counters["instructions"], counters["rep_bytes"]), (12345, 64, 7))
                self.assertEqual(counters.get("load_instructions"), 9 if enabled else None)

    def test_inconsistent_or_missing_probe_rejected(self):
        for i, change in enumerate(({"pid": self.pid + 1}, {"threads": 2}, {"instructions": 63},
                                    {"load_instructions": 65}, {"load_instructions": True})):
            with self.subTest(change=change), patch.dict(self.loads, change):
                with self.assertRaisesRegex(ValueError, "probe"):
                    self.measure(f"bad-probe-{i}")
        with self.assertRaises((OSError, ValueError)):
            self.measure("missing-probe", produce_probe=False)

    def test_unqualified_production_receipt_rejected(self):
        failures = ({"complete": False}, {"quality": "signals_observed"}, {"image_count": 2},
                    {"process_count": 2}, {"cpuid_profile": "x86-64-v1"}, {"segments": [{"threads": 2}]},
                    {"unsupported_rep_entries": 1}, {"latency_bound": {"version": 2}})
        for i, change in enumerate(failures):
            with self.subTest(change=change), patch.dict(self.receipt, change):
                with self.assertRaisesRegex(ValueError, "production receipt"):
                    self.measure(f"bad-receipt-{i}")

    def test_child_stdout_and_input_are_checked(self):
        _, cpu_ns = collector.checked_run(["/bin/cat"], self.stdin, self.stdin.read_bytes(), 5)
        self.assertGreater(cpu_ns, 0)
        with self.assertRaisesRegex(ValueError, "stdout mismatch"):
            collector.checked_run(["/bin/cat"], self.stdin, b"different", 5)


if __name__ == "__main__":
    unittest.main()
