"""Retrospective replay of the bit-reversed false pass that motivated the Wall range study."""
from fractions import Fraction
from hashlib import sha256
import json
from math import ceil
from pathlib import Path
from statistics import median
import sys
import tarfile
import unittest

EXPERIMENTS = Path(__file__).resolve().parents[1] / "experiments"
sys.path.insert(0, str(EXPERIMENTS))
from contest_branch import corpus as point_updates
from wall_locality.pages import reference_counts
from wall_locality.score import predict


def counts_from_profile(raw):
    lines = raw.decode().splitlines()
    fields = next(line.split()[1:] for line in lines if line.startswith("events:"))
    return dict(zip(fields, map(int, lines[-1].split()[1:]), strict=True))


class PreviousPointUpdates(unittest.TestCase):
    def test_bit_reversed_correction_on_both_previous_guests(self):
        plan = json.loads((EXPERIMENTS / "contest_branch/plan.json").read_bytes())
        pages = {}
        for case in plan["cases"]:
            name = case["name"]
            text, _ = point_updates.make(name)
            self.assertEqual(sha256(text.encode()).hexdigest(), case["stdin_sha256"])
            pages[name] = reference_counts(text)
        self.assertEqual({n: v["misses"] for n, v in pages.items()},
                         dict(ascending=256, shuffled=122949, tiles32=256, tiles4096=256, bit_reversed=131072))
        for suffix, expected in (("", 56_443_000), ("-repeat", 56_979_000)):
            path = EXPERIMENTS / "results" / ("gce-wall-branch" + suffix) / "output.tar.gz"
            features = {name: dict(leaf_pages=count) for name, count in pages.items()}
            native = {name: [] for name in pages}
            with tarfile.open(path) as tar:
                rows = json.load(tar.extractfile("output/run.json"))["rows"]
                seal = json.load(tar.extractfile("output/predictions.json"))["cases"]
                for row in rows:
                    name, arm = row["name"], row["arm"]
                    if arm == "native":
                        self.assertEqual(row["cpu_us"], row["user_us"] + row["sys_us"])
                        native[name].append(1000 * row["cpu_us"])
                    elif row["rep"] == 1:
                        prefix = f"output/runs/{row['sequence']:03d}/"
                        if arm == "ordinary":
                            record = prefix + f"records/process-{row['launcher_pid']}.jsonl"
                            last = json.loads(tar.extractfile(record).read().splitlines()[-1])
                            features[name][arm] = {k: last[k] for k in ("instructions", "rep_bytes")}
                        else:
                            features[name][arm] = counts_from_profile(tar.extractfile(prefix + "cachegrind.out").read())

            a, b = ("ascending", "shuffled")
            low, high = median(native[a]), median(native[b])
            for name in ("tiles32", "tiles4096", "bit_reversed"):
                fractions = []
                for arm, event in (("branch_only", "Bcm"), ("leaf_pages", "misses")):
                    start, stop, x = [features[n][arm][event] for n in (a, b, name)]
                    fractions.append(max(0, min(1, Fraction(x - start, stop - start))))
                estimate = ceil(low + max(fractions) * (high - low))
                actual = predict(dict(features[a], native_ns=native[a]), dict(features[b], native_ns=native[b]),
                                 features[name])
                self.assertEqual(estimate, actual["branch_pages"])
                old = seal[name]["wall"]["branch_misses"]
                self.assertEqual(old, actual["branch_misses"])
                if name == "bit_reversed":
                    self.assertEqual(estimate, expected)
                    self.assertLessEqual(old, 50_000_000)
                    self.assertGreater(estimate, 50_000_000)
                    self.assertEqual(sum(t > 50_000_000 for t in native[name]), 9)
                else:
                    self.assertEqual(old, estimate)


if __name__ == "__main__":
    unittest.main()
