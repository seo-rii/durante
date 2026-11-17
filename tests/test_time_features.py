"""Pure logic of the exploratory time-feature experiment."""
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("time_features", ROOT / "experiments/time_features.py")
time_features = importlib.util.module_from_spec(spec)
spec.loader.exec_module(time_features)

SAMPLE = """Cache simulation results:
Core #0 (1 thread(s))
  L1I0 (size=32768, assoc=8, block=64, LRU) stats:
    Hits:                         60859067
    Misses:                            728
  L1D0 (size=32768, assoc=8, block=64, LRU) stats:
    Hits:                          1917302
    Misses:                       13300119
    Prefetch hits:                  945769
LL (size=8388608, assoc=16, block=64, LRU) stats:
    Hits:                         13297482
    Misses:                           3365
"""


class TimeFeatureTests(unittest.TestCase):
    def test_cache_report_parsing_keeps_demand_hits_and_misses(self):
        stats = time_features.cache_stats(SAMPLE)
        self.assertEqual(stats["l1i"], {"hits": 60859067, "misses": 728})
        self.assertEqual(stats["l1d"], {"hits": 1917302, "misses": 13300119})
        self.assertEqual(stats["ll"], {"hits": 13297482, "misses": 3365})

    @unittest.skipUnless(importlib.util.find_spec("numpy"), "numpy is required")
    def test_nnls_recovers_nonnegative_weights_and_clamps_negative_ones(self):
        import numpy as np
        a = np.array([[1.0, 0.0], [0.0, 1.0], [1.0, 1.0], [2.0, 1.0]])
        self.assertTrue(np.allclose(time_features.nnls(a, a @ np.array([3.0, 0.5])), [3.0, 0.5]))
        weights = time_features.nnls(a, a @ np.array([2.0, -1.0]))
        self.assertTrue((weights >= 0).all())


if __name__ == "__main__":
    unittest.main()
