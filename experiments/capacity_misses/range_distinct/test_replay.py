import unittest

import replay


class RangeDistinctReplay(unittest.TestCase):
    def test_consensus_and_held_out_candidate(self):
        row = dict(ML4="accept", ML4_plus_misses="accept", dependent="accept", ML4_plus_3_3="timeout")
        self.assertEqual(replay.unanimity(row), "accept")
        self.assertEqual(replay.unanimity(dict(row, dependent="timeout")), "abstain_disagreement")
        self.assertEqual(replay.unanimity(dict(row, dependent="indeterminate")), "abstain_mixed_model")
        d = {"test": {}}
        for f, (native, dep) in enumerate([("timeout","accept"), ("accept","timeout"), ("indeterminate","accept")]):
            case = dict(row, dependent=dep)
            d["test"][str(f)] = dict(case, native=native, unanimous=replay.unanimity(case))
        c = replay.summarize(d)["unanimity"]
        self.assertEqual((c["all_cells"], c["issued"], c["native_decisive"], c["issued_native_decisive"],
                          c["false_accept"], c["issued_native_mixed"], c["abstain_disagreement"]),
                         (3,2,2,1,1,1,1))

    def test_published_results(self):
        self.assertEqual(replay.replay(), replay.read(replay.HERE / "results.json"))
