"""Two orders of the same inclusive queries and an independent per-value occurrence oracle."""
from bisect import bisect_left
import random

SIZE = 32768
COLORS = 256
SEED = 20261130
WINDOW = 512


def workload(order):
    rng = random.Random(SEED)
    data = [rng.randrange(COLORS) for _ in range(SIZE)]
    queries = []
    for i in range(SIZE):
        left = rng.randrange(SIZE)
        if i % 2:
            right = min(SIZE - 1, left + rng.randrange(96))
        else:
            right = rng.randrange(SIZE)
        queries.append((min(left, right), max(left, right)))
    if order == "runs":
        for start in range(0, SIZE, WINDOW):
            queries[start:start + WINDOW] = sorted(queries[start:start + WINDOW], reverse=(start // WINDOW) % 2 == 1)
    elif order != "shuffled":
        raise ValueError("unknown query ordering")
    return data, queries


def encode(data, queries):
    lines = [f"{len(data)} {len(queries)}", " ".join(map(str, data))]
    lines.extend(f"{l} {r}" for l, r in queries)
    return ("\n".join(lines) + "\n").encode("ascii")


def oracle(data, queries):
    positions = {}
    for i, v in enumerate(data):
        positions.setdefault(v, []).append(i)
    # A value is present exactly when its first position >= left is <= right.
    answers = []
    for left, right in queries:
        count = 0
        for indices in positions.values():
            i = bisect_left(indices, left)
            count += i < len(indices) and indices[i] <= right
        answers.append(f"{count}\n")
    return "".join(answers).encode("ascii")
