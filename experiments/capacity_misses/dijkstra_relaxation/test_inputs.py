import unittest

from inputs import BASE, answer, edges, encode


class GraphTest(unittest.TestCase):
    def test_paired_topology_and_ordinal_improvements(self):
        for centers, targets in [(1, 1), (2, 6), (5, 11)]:
            samples = []
            for order in ("once", "many"):
                graph = list(edges(order, centers, targets))
                samples.append([(u, v) for u, v, _ in graph])
                n = centers + targets + 1
                distances = [None] * (n + 1)
                distances[1] = 0
                updates = [0] * (n + 1)
                # The edge generator emits a topological order.
                for u, v, w in graph:
                    candidate = distances[u] + w
                    if distances[v] is None or distances[v] > candidate:
                        updates[v] += 1
                        distances[v] = candidate
                self.assertEqual(updates[centers + 2:], [1 if order == "once" else centers] * targets)
                self.assertEqual(distances[centers + 2:], list(range(BASE, BASE + targets)))
                self.assertEqual(answer(centers, targets), f"{distances[-1]} {n} {sum(distances[1:])}\n".encode())
            self.assertEqual(samples[0], samples[1])
            self.assertEqual(len(encode("once", centers, targets)), len(encode("many", centers, targets)))
