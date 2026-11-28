import json
import tarfile
import unittest

import graph_evidence
import replay


class StageStudyTests(unittest.TestCase):
    def test_registered_measurements(self):
        report = replay.replay()
        self.assertEqual(report, replay.read(replay.HERE / "results.json"))
        for case in report["pooled"].values():
            self.assertEqual(case["samples"], dict.fromkeys(replay.MODES, 10))
            self.assertEqual(set(case["distinct_readings_per_counter"].values()), {1})

    def test_cache_modes_require_the_requested_diagnostic(self):
        with tarfile.open(replay.prepare.PARENT / "evidence/primary.tar.gz") as archive:
            samples = archive.extractfile("output/trials.jsonl").read().splitlines()
            trial = next(r for line in samples if (r := json.loads(line))["arm"] == "probe")
            i, pid = trial["index"], trial["pid"]
            process = archive.extractfile(f"output/records-{i:03d}/process-{pid}.jsonl").read()
            full = archive.extractfile(f"output/probe-{i:03d}.txt").read()
        l1 = full.splitlines(keepends=True)[0]
        baseline = graph_evidence.decode(pid, "outer", process, full)
        shared = graph_evidence.decode(pid, "l1d", process, l1)
        self.assertEqual(shared, {k:v for k,v in baseline.items() if k != "outer"})
        with self.assertRaisesRegex(ValueError, "different cache mode"):
            graph_evidence.decode(pid, "outer", process, l1)
        with self.assertRaisesRegex(ValueError, "incorrect latency capability"):
            graph_evidence.decode(pid, "rep", process, b"")

    def test_signed_differences_and_full_cross_sample_range(self):
        cpus = {"native": [10, 12], "rep": [30, 32], "latency": [25, 29],
                "l1d": [60, 70], "outer": [99, 101]}
        arms = {arm:[dict(cpu_us=t, counters={"example": 1}) for t in a] for arm,a in cpus.items()}
        for key in ("l1d", "outer"):
            for record in arms[key]:
                record["counters"].update(l1d_misses=3, l1d_lines=10)
        arms["rep"][0]["counters"]["example"] = 2
        report = replay.aggregate(arms)
        self.assertEqual(report["l1d_line_hit_percent"], [70, 70])
        self.assertEqual(report["stages"]["latency"]["delta_us"], -4)
        self.assertEqual(report["stages"]["latency"]["delta_range_us"], [-7, -1])
        self.assertAlmostEqual(sum(row["percent_excess_over_native"] for row in report["stages"].values()),
                               100, delta=0.003)
        self.assertEqual(report["distinct_readings_per_counter"]["example"], 2)


if __name__ == "__main__":
    unittest.main()
