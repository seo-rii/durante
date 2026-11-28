import unittest

import replay


class JointBoundsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.counts, cls.native, cls.frozen = replay.observations()

    def test_original_misses_and_witness(self):
        _, original = replay.evaluate(self.counts, self.native, self.frozen)
        self.assertCountEqual(original, [("uniform-fenwick", "1"), ("clustered-fenwick", "1"), ("block2048", "3")])
        counts, misses = replay.evaluate(self.counts, self.native, replay.WITNESS)
        self.assertEqual(misses, [])
        self.assertEqual(counts, {(name, True):n for name,n in [
            ("predecessor registered", 16), ("predecessor parity", 4), ("graph multipliers", 9), ("graph 65 ms", 2)]})
        for p in ("uniform", "clustered"):
            self.assertLess(replay.dot(self.counts[p+"-fenwick"], replay.WITNESS),
                            replay.dot(self.counts[p+"-dsu"], replay.WITNESS))

    def test_strictly_negative_certificate(self):
        vector = replay.certificate(self.counts, self.native)
        self.assertEqual(vector, (-496647520, -45632, -75279744, -2775627, -45817, -2933082, -3243427, -483888))
        modified = dict(self.counts, block8=tuple(100*v for v in self.counts["block8"]))
        with self.assertRaisesRegex(ValueError, "nonnegative coordinate"):
            replay.certificate(modified, self.native)


if __name__ == "__main__":
    unittest.main()
