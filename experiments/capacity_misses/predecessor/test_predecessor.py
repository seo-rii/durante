import unittest

import prepare
import replay


class PredecessorSuite(unittest.TestCase):
    def test_duplicate_sale_and_empty_stock(self):
        self.assertEqual(prepare.oracle([7, 1, 7], [0, 7, 6, 8, 7]), b"-1\n7\n1\n7\n-1\n")
        self.assertEqual(prepare.oracle([], [0, 1]), b"-1\n-1\n")

    def test_many_orders_match_literal_sale(self):
        import itertools
        for prices in itertools.product((1, 3), repeat=3):
            for budgets in itertools.product((0, 2, 5), repeat=4):
                inventory = list(prices)
                answers = []
                for b in budgets:
                    sold = max((price for price in inventory if price <= b), default=-1)
                    if sold >= 0:
                        inventory.remove(sold)
                    answers.append(f"{sold}\n")
                self.assertEqual(prepare.oracle(prices, budgets), "".join(answers).encode())

    def test_input_pair(self):
        protocol = replay.read(replay.HERE / "protocol.json")
        p1, b1 = prepare.workload(protocol, "uniform")
        p2, b2 = prepare.workload(protocol, "clustered")
        self.assertEqual(p1, p2)
        self.assertEqual(sorted(b1), sorted(b2))
        self.assertNotEqual(b1, b2)
        self.assertNotEqual(prepare.oracle(p1, b1), prepare.oracle(p2, b2))

    def test_registered_observations(self):
        self.assertEqual(replay.replay(), replay.read(replay.HERE / "results.json"))
