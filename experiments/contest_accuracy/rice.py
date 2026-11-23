"""Original field distributions for IOI 2011 Rice Hub's besthub function."""
import random

CASES = ("uniform", "duplicate_clusters", "scattered_gaps", "wide_coordinates")


def best_window(positions, budget):
    """Maintain the median cost as a window grows or sheds its leftmost field."""
    left = cost = longest = 0
    for right, position in enumerate(positions):
        cost += position - positions[(left + right) // 2]
        while cost > budget:
            cost -= positions[(left + right + 1) // 2] - positions[left]
            left += 1
        longest = max(longest, right - left + 1)
    return longest


def make(name):
    if name == "uniform":
        positions = list(range(1, 60_001))
        budget = 2_400_000
    elif name == "duplicate_clusters":
        positions = [i // 197 * 23 + 1 for i in range(62_000)]
        budget = 0
    elif name == "scattered_gaps":
        rng = random.Random(18943)
        position = 1
        positions = []
        for _ in range(52_000):
            position += rng.choice((0, 1, 1, 7, 29, 353))
            positions.append(position)
        budget = 13_000_000
    elif name == "wide_coordinates":
        # Large gaps between clusters exercise the full 64-bit cost arithmetic.
        positions = [1 + i // 8192 * 190_000_000 + i % 8192 for i in range(47_000)]
        budget = 1_900_000_000_000
    else:
        raise KeyError(name)
    header = f"{len(positions)} {positions[-1]} {budget}\n"
    data = header + "".join(f"{position}\n" for position in positions)
    return data, f"{best_window(positions, budget)}\n"
