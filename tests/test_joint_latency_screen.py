import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch


with patch.object(sys, "path", [str(Path(__file__).resolve().parents[1] / "experiments"), *sys.path]):
    import joint_latency_screen as study


class JointLatencyTests(unittest.TestCase):
    def test_archive_reconstructs_all_samples_and_proves_extreme_time_conflict(self):
        data = study.recover(study.ARCHIVE)
        result = study.screen(data)
        bounds = result["latency_bound_x16"]
        self.assertEqual(len(bounds), 27)
        self.assertTrue(all(len(repetitions) == 3 for repetitions in bounds.values()))
        self.assertEqual(bounds["integer-4"], [8590535988] * 3)
        self.assertEqual(bounds["window_sum-4"], [3491636912] * 3)
        self.assertTrue(result["joint_accuracy_impossible"])
        self.assertEqual(result["feature_slack_2sort_plus_3merge_minus_5search"], {
            "instructions": 205842131, "rep_bytes": 0, "load_instructions": 89586248,
            "gshare_mispredicts": 372879, "latency_bound_x16": 823369020})
        self.assertEqual(result["minimum_search_time_ns_x25"], 25 * 83992000)
        self.assertEqual(result["maximum_mixture_time_ns_x25"], 25 * 81383040)

        # A single counter extreme can invalidate domination despite equal medians.
        search = next(c for c in data["cases"] if c["family"] == "lower_bound" and c["scale"] == 4)
        search["repetitions"][0]["latency_bound_x16"] *= 2
        self.assertFalse(study.screen(data)["joint_accuracy_impossible"])

    def test_expanded_conflict_uses_all_nineteen_counter_ranges(self):
        data = study.recover(study.ARCHIVE)
        expanded = study.screen(data)["full_counter_screen"]
        self.assertEqual(expanded["verified_repetitions"], 81)
        self.assertTrue(expanded["joint_accuracy_impossible"])
        self.assertEqual(expanded["mixture_weights"], {"sort-4": 23, "branch-2": 8, "merge_streams-1": 29})
        self.assertEqual(expanded["feature_slacks"], {
            "instructions": 4180683048, "rep_bytes": 0, "load_instructions": 1095517886,
            "gshare_mispredicts": 459943, "latency_bound_x16": 16722738416, "conditional_branches": 749899650,
            "stores": 944808957, "direct_jumps_calls": 14161697, "indirect_branches": 273468538,
            "muls": 330297443, "divs": 0, "fp_ops": 9076503, "critical_path_cycles_x16": 66304859680,
            "loop_recurrence_cycles_x16": 10260142720, "rep_entries": 0, "rep_movs_bytes": 0, "rep_stos_bytes": 0,
            "rep_movs_entries": 0, "rep_stos_entries": 0,
        })
        self.assertEqual(expanded["time_bound_denominator"], 300)
        self.assertEqual(expanded["minimum_target_time_numerator_ns"], 300 * 83992000)
        self.assertEqual(expanded["maximum_mixture_time_numerator_ns"], 300 * 83646200)

        # One extra division in a single search repetition breaks only the expanded inequality.
        search = next(c for c in data["cases"] if c["family"] == "lower_bound" and c["scale"] == 4)
        search["repetitions"][0]["divs"] += 1
        result = study.screen(data)
        self.assertTrue(result["joint_accuracy_impossible"])
        self.assertEqual(result["full_counter_screen"]["feature_slacks"]["divs"], -60)
        self.assertFalse(result["full_counter_screen"]["joint_accuracy_impossible"])

    def test_touching_native_intervals_do_not_prove_impossibility(self):
        data = study.recover(study.ARCHIVE)
        for case in data["cases"]:
            # 4/5 * 3 equals 6/5 * 2. These test durations make the two
            # prediction intervals meet without changing any counter.
            duration = 3 if (case["family"], case["scale"]) == ("lower_bound", 4) else 2
            for row in case["repetitions"]:
                row["native_before_ns"] = row["native_after_ns"] = duration
        expanded = study.screen(data)["full_counter_screen"]
        self.assertEqual(expanded["minimum_target_time_numerator_ns"], expanded["maximum_mixture_time_numerator_ns"])
        self.assertFalse(expanded["joint_accuracy_impossible"])

    def test_production_bound_requires_same_run_feature_and_published_repetition(self):
        row = json.loads(study.REFERENCE.read_bytes())["cases"][0]["repetitions"][0]
        folder = "results/integer-1-1-probe/"
        with tarfile.open(study.ARCHIVE) as archive:
            path, = (p for p in archive.getnames() if p.startswith(folder + "records/"))
            pid = int(Path(path).stem.removeprefix("process-"))
            raw = archive.extractfile(path).read()
            features = json.load(archive.extractfile(folder + "features.json"))
        for source, probe in [
            ({**row, "rep_bytes": row["rep_bytes"] + 1}, features),
            (row, {**features, "bound_cycles_x16": features["bound_cycles_x16"] + 1}),
            (row, {**features, "mispredicts": features["mispredicts"] - 1}),
            (row, {**features, "stores": False}),
            (row, {**features, "critical_path_cycles_x16": -1}),
        ]:
            with self.subTest(source=source, probe=probe), self.assertRaises(ValueError):
                study.recover_bound(source, probe, pid, raw)

    def test_rejects_unrecognized_archive_before_decompression(self):
        with tempfile.TemporaryDirectory() as tmp:
            broken = Path(tmp) / "records.tar.gz"
            broken.write_bytes(study.ARCHIVE.read_bytes()[:-1])
            with self.assertRaisesRegex(ValueError, "unexpected CPP20 record archive"):
                study.recover(broken)


if __name__ == "__main__":
    unittest.main()
