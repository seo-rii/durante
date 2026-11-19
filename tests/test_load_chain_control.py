"""Check the frozen load-dependency control and its two timing comparisons."""
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import unittest

EXPERIMENTS = Path(__file__).resolve().parents[1] / "experiments"
sys.path.insert(0, str(EXPERIMENTS))
from joint_latency_screen import FULL_COUNTERS


class LoadChainControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads((EXPERIMENTS / "results/cpp20-load-chain-control.json").read_bytes())

    def assert_endpoint_bound(self, recorded, fast_max, slow_min):
        self.assertEqual(recorded["faster_case_max_ns"], fast_max)
        self.assertEqual(recorded["slower_case_min_ns"], slow_min)
        bound = Fraction(slow_min - fast_max, slow_min + fast_max)
        self.assertEqual(bound, Fraction(**recorded["min_relative_error"]))
        return bound

    @unittest.skipUnless(sys.platform == "linux" and platform.machine() == "x86_64" and shutil.which("cc"),
                         "requires Linux x86-64 and a native assembler/linker")
    def test_frozen_program_checks_both_chains_and_rejects_unknown_input(self):
        fixture = self.data["fixture"]
        source = EXPERIMENTS / "load_chain_control.S"
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), fixture["source_sha256"])
        self.assertEqual(fixture["nodes"] * fixture["visits_per_node"],
                         fixture["loop_iterations"] * fixture["loads_per_iteration"])
        with tempfile.TemporaryDirectory() as folder:
            binary = Path(folder) / "load-chain"
            # The original unstripped ELF records this basename in its file symbol.
            subprocess.run(["cc", "-c", str(source), "-o", "cc0JYjty.o"], cwd=folder, check=True)
            subprocess.run(["cc", "-nostdlib", "-static", "-no-pie", "-Wl,--build-id=none",
                            "cc0JYjty.o", "-o", str(binary)], cwd=folder, check=True)
            linker = subprocess.check_output(["cc", "-print-prog-name=ld"], text=True).strip()
            version = subprocess.check_output([linker, "--version"], text=True).splitlines()[0]
            if version.startswith("GNU ld ") and version.endswith(" 2.40"):
                self.assertEqual(hashlib.sha256(binary.read_bytes()).hexdigest(), fixture["elf_sha256"])
            for mode in ("serial", "parallel"):
                with self.subTest(mode=mode):
                    stdin = fixture["stdin"][mode].encode()
                    self.assertEqual(hashlib.sha256(stdin).hexdigest(), fixture["stdin_sha256"][mode])
                    run = subprocess.run([binary], input=stdin,
                                         capture_output=True, check=True, timeout=10)
                    self.assertEqual(run.stdout, fixture["stdout"].encode())
                    self.assertEqual(hashlib.sha256(run.stdout).hexdigest(), fixture["stdout_sha256"])
                    self.assertEqual(run.stderr, b"")
            for invalid in (b"", b"2"):
                run = subprocess.run([binary], input=invalid, capture_output=True, timeout=10)
                self.assertEqual((run.returncode, run.stdout), (1, b""))

    def test_same_live_counts_at_a_limit_that_separates_native_verdicts(self):
        cloud = self.data["cloud_run"]
        model = cloud["model"]
        # REP work is zero for both modes, so only instruction weight contributes.
        denominator = model["ns_denominator"]
        estimate = (56_361_019 * model["instruction_ns_numerator"] + denominator - 1) // denominator
        native = {}
        for mode in ("serial", "parallel"):
            rows = cloud["rounds"][mode]
            self.assertEqual(sorted(row["repetition"] for row in rows), [1, 2, 3])
            for row in rows:
                self.assertEqual((row["instructions"], row["movs_bytes"], row["stos_bytes"]),
                                 (56_361_019, 0, 0))
                self.assertEqual(row["predicted_ns"], estimate)
            native[mode] = sorted(row[field] for row in rows for field in ("native_before_ns", "native_after_ns"))
        fast_max, slow_min = max(native["parallel"]), min(native["serial"])
        # Even the nearest measured endpoints require this error for one shared prediction.
        bound = self.assert_endpoint_bound(cloud["same_clock_bound"], fast_max, slow_min)
        self.assertGreater(bound, Fraction(59, 100))
        medians = [Fraction(ns[2] + ns[3], 2) for ns in native.values()]
        midpoint_ms = int((sum(medians) + 1_000_000) // 2_000_000)
        self.assertEqual(midpoint_ms, cloud["common_limit_ms"])
        self.assertLess(estimate, midpoint_ms * 1_000_000)
        self.assertLess(fast_max, midpoint_ms * 1_000_000)
        self.assertGreater(slow_min, midpoint_ms * 1_000_000)
        for mode, verdict in (("serial", "Time Limit Exceeded"), ("parallel", "Accepted")):
            boundary = cloud["boundaries"][mode]
            self.assertEqual(boundary["native"], [verdict] * 3)
            self.assertEqual((boundary["live"], boundary["termination"]), ("Accepted", "stdout"))

    def test_diagnostic_fields_that_distinguish_the_controls(self):
        diagnostics = self.data["diagnostic_probe"]
        self.assertEqual(diagnostics["repetitions_per_mode"], 3)
        serial, parallel = (diagnostics["counters"][mode] for mode in ("serial", "parallel"))
        for vector in (serial, parallel):
            self.assertEqual(set(vector), set(FULL_COUNTERS))
        different = {key for key in FULL_COUNTERS if serial[key] != parallel[key]}
        self.assertEqual(different, {"critical_path_cycles_x16", "latency_bound_x16", "loop_recurrence_cycles_x16"})
        self.assertEqual(different, set(diagnostics["fields_differing"]))
        for key in different:
            self.assertGreater(serial[key], parallel[key], key)

    def test_archived_reversal_uses_the_whole_counter_vector(self):
        archive = self.data["archived_comparison"]
        cases = archive["cases"]
        linked, branch = cases["linked-rank"], cases["branch-2"]
        bound = self.assert_endpoint_bound(archive["monotone_model_bound"], max(branch["native_ns"]),
                                           min(linked["native_ns"]))
        self.assertGreater(bound, Fraction(1, 5))
        for case in (linked, branch):
            self.assertEqual(set(case["counters"]), set(FULL_COUNTERS))
        reversed_fields = {key for key in FULL_COUNTERS if linked["counters"][key] > branch["counters"][key]}
        self.assertEqual(reversed_fields, {"fp_ops", "loop_recurrence_cycles_x16"})
        self.assertEqual(reversed_fields, set(archive["fields_with_linked_rank_greater"]))
        self.assertTrue(reversed_fields.isdisjoint(archive["frozen_model_fields"]))

    def test_recurrence_benchmark_covers_the_frozen_inputs_and_paired_timings(self):
        cost = json.loads((EXPERIMENTS / "results/loop-recurrence-cost.json").read_bytes())
        self.assertEqual(cost["schema"], 1)
        self.assertEqual(cost["environment"]["pairs"], 6)
        tools = cost["sha256"]
        reference_tools = self.data["diagnostic_probe"]["tools"]
        for name in ("drrun", "meter", "drmgr", "release_core"):
            self.assertEqual(tools[name], reference_tools["runtime" if name == "release_core" else name])
        for name in ("probe", "probe_source", "harness"):
            self.assertRegex(tools[name], r"^[0-9a-f]{64}$")

        fixture = self.data["fixture"]
        identities = {mode: (fixture["elf_sha256"], fixture["stdin_sha256"][mode], fixture["stdout_sha256"])
                      for mode in ("serial", "parallel")}
        manifest = (EXPERIMENTS / "branch_load_holdout/manifest.json").read_bytes()
        protocol = json.loads((EXPERIMENTS / "results/cpp20-v8-protocol.json").read_bytes())
        self.assertEqual(hashlib.sha256(manifest).hexdigest(), protocol["external"]["manifest_sha256"])
        for name, case in json.loads(manifest)["cases"].items():
            identities[name] = (case["elf_sha256"], case["stdin_sha256"], case["stdout_sha256"])
        families = json.loads((EXPERIMENTS / "load_corpus.json").read_bytes())["families"]
        for name in ("integer", "branch", "sort", "rep_copy", "rep_fill"):
            self.assertEqual(cost["cases"][name]["scale"], 2)
            family = families[name]
            case = next(case for case in family["cases"] if case["scale"] == 2)
            identities[name] = (family["binary_sha256"], hashlib.sha256(case["stdin"].encode()).hexdigest(),
                                hashlib.sha256(case["stdout"].encode()).hexdigest())

        self.assertEqual(set(cost["cases"]), set(identities))
        for name, case in cost["cases"].items():
            with self.subTest(case=name):
                self.assertEqual((case["program_sha256"], case["stdin_sha256"], case["stdout_sha256"]),
                                 identities[name])
                self.assertEqual(set(case["counters"]), {"instructions", "rep_bytes", "latency_bound_x16",
                                                         "loop_recurrence_cycles_x16"})
                self.assertTrue(all(type(n) is int and 0 <= n < 2**64 for n in case["counters"].values()))
                self.assertEqual(set(case["child_cpu_ns"]), {"full", "recurrence_only"})
                for samples in case["child_cpu_ns"].values():
                    self.assertEqual(len(samples), cost["environment"]["pairs"])
                    self.assertTrue(all(type(ns) is int and ns > 0 for ns in samples))

        vectors = dict(self.data["diagnostic_probe"]["counters"])
        archived = self.data["archived_comparison"]["cases"]
        for name, archived_name in (("linked-rank", "linked-rank"), ("branch", "branch-2")):
            row = archived[archived_name]
            self.assertEqual((row["elf_sha256"], row["input_sha256"], row["output_sha256"]), identities[name])
            vectors[name] = row["counters"]
        for name, vector in vectors.items():
            self.assertEqual(cost["cases"][name]["counters"]["loop_recurrence_cycles_x16"],
                             vector["loop_recurrence_cycles_x16"], name)


if __name__ == "__main__":
    unittest.main()
