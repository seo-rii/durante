"""Print independently calculated case IDs and expected stdout. No workload binaries required."""
import argparse
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MASK = (1 << 32) - 1


def args(case):
    return list(map(int, (ROOT / case / "in").read_text().split()))


def words(seed):
    while True:
        seed ^= (seed << 13) & MASK
        seed ^= seed >> 17
        seed ^= (seed << 5) & MASK
        yield seed


def crossed():
    inputs = {folder.name: args(folder.name) for folder in sorted(ROOT.glob("cross-rep*"))}
    sizes = sorted({size for _, size in inputs.values()})
    totals = {}
    # Directly evaluate the mathematical table value for each visited index.
    for size in sizes:
        rng = words(193177)
        totals[size] = sum(((next(rng) % size) * 2654435761 + 12345) % (1 << 32)
                           for _ in range(1 << 23))
    for name, (count, size) in inputs.items():
        value = (29 * 471 + 7) if count else (37 * 471 + 11)
        print(name, totals[size], value % 256)


def sparse_rmq():
    size, queries, seed = args("sparse-rmq")
    rng = words(seed)
    data = [next(rng) for _ in range(size)]
    total = 0
    for _ in range(queries):
        left = next(rng) % size
        length = 1 + next(rng) // (1 << 26)
        total += min(data[left:left + length])
    return total


def histogram_stack():
    width, rounds, seed = args("histogram-stack")
    rng = words(seed)
    total = 0
    for _ in range(rounds):
        heights = [1 + next(rng) // (1 << 16) for _ in range(width)]
        # length records the lengths at the two ends of each active component.
        # In descending height order its union has area >= length * new height.
        length = [0] * (width + 2)
        best = 0
        for index in sorted(range(width), key=heights.__getitem__, reverse=True):
            pos = index + 1
            left, right = length[pos - 1], length[pos + 1]
            joined = 1 + left + right
            length[pos - left] = length[pos + right] = joined
            best = max(best, joined * heights[index])
        total += best
    return total


def distinct_window():
    count, alphabet, seed = args("distinct-window")
    queue, present = deque(), set()
    rng = words(seed)
    total = 0
    for _ in range(count):
        symbol = next(rng) % alphabet
        while symbol in present:
            present.remove(queue.popleft())
        present.add(symbol)
        queue.append(symbol)
        total += len(queue)
    return total


if __name__ == "__main__":
    cases = {"sparse-rmq": sparse_rmq, "histogram-stack": histogram_stack, "distinct-window": distinct_window}
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("group", choices=["all", "crossed", *cases], nargs="?", default="all")
    group = parser.parse_args().group
    if group in ("all", "crossed"):
        crossed()
    for name, oracle in cases.items():
        if group in ("all", name):
            print(name, oracle())
