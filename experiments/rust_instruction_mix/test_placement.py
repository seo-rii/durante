import copy
import gzip
import json
from pathlib import Path
import unittest

from placement import LOADER, placement


class PlacementTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        results = Path(__file__).resolve().parents[1] / "results/gce-rust-instruction-mix"
        cls.dataset = json.loads(gzip.decompress((results / "dataset.json.gz").read_bytes()))
        cls.receipt = json.loads(gzip.decompress((results / "placement.json.gz").read_bytes()))

    def test_every_loader_is_bound_to_its_measured_child(self):
        placement(self.dataset, self.receipt)
        mutated = copy.deepcopy(self.receipt)
        loader = next(event for event in mutated["events"] if event[0] == "exec" and event[4] == LOADER)
        loader[3] = self.dataset["samples"][1]["metered"]["pid"]
        with self.assertRaisesRegex(ValueError, "lifecycle"):
            placement(self.dataset, mutated)

    def test_one_wrong_cpu_among_180_execs_rejects_placement(self):
        mutated = copy.deepcopy(self.receipt)
        native = next(event for event in mutated["events"] if event[0] == "exec" and event[4].endswith("/Main"))
        native[2] = 0
        with self.assertRaisesRegex(ValueError, "misplaced"):
            placement(self.dataset, mutated)


if __name__ == "__main__":
    unittest.main()
