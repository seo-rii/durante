"""The committed Java timing summary is the summarizer's output for the committed samples."""
import importlib.util
import json
from pathlib import Path
import unittest

STUDY = Path(__file__).resolve().parents[1] / "experiments/java_timing"
spec = importlib.util.spec_from_file_location("java_timing_summary", STUDY / "summarize.py")
timing = importlib.util.module_from_spec(spec)
spec.loader.exec_module(timing)


class CommittedJavaTiming(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.results = json.loads((STUDY / "results.json").read_text())
        cls.summary = json.loads((STUDY / "summary.json").read_text())

    def test_summary_is_derived_from_the_samples(self):
        self.assertEqual(timing.rounded(timing.summarize(self.results)), self.summary)

    def test_samples_are_the_full_attribution_run(self):
        provenance = self.results["provenance"]
        self.assertEqual(provenance["rounds"], 5)
        self.assertEqual(len(provenance["input_sha256"]), 7)
        self.assertEqual(self.results["langs"], {"java": ["main", "runtime", "client", "both"],
                                                 "java-cds": ["main", "both"]})
        configs = provenance["configs"]
        # The four configurations cross two runtimes with two clients.
        runtime = {name: c["libdynamorio_release_sha256"] for name, c in configs.items()}
        client = {name: c["client_sha256"] for name, c in configs.items()}
        self.assertEqual(runtime["main"], runtime["client"])
        self.assertEqual(runtime["runtime"], runtime["both"])
        self.assertNotEqual(runtime["main"], runtime["both"])
        self.assertEqual(client["main"], client["runtime"])
        self.assertEqual(client["client"], client["both"])
        self.assertNotEqual(client["main"], client["both"])
        for name, evidence in (("main", "runtime-main.json"), ("both", "runtime-branch.json")):
            manifest = json.loads((STUDY / "evidence" / evidence).read_text())
            self.assertEqual(configs[name]["runtime_inputs"], manifest["inputs"])
            self.assertEqual(runtime[name], manifest["artifacts"]["lib64/release/libdynamorio.so"])


if __name__ == "__main__":
    unittest.main()
