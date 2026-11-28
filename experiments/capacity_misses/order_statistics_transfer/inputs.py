"""Deterministic inputs and an independent strict inversion-count oracle."""
import random

COUNT = 262144
SEED = 20261129
WINDOW = 512


def workload(pattern):
    if pattern not in ("shuffled", "runs"):
        raise ValueError("unknown inversion input pattern")
    rng = random.Random(SEED)
    values = [rng.randint(1, COUNT) for _ in range(COUNT)]
    if pattern == "runs":
        for start in range(0, COUNT, WINDOW):
            descending = (start // WINDOW) % 2 == 1
            values[start:start + WINDOW] = sorted(values[start:start + WINDOW], reverse=descending)
    return values


def encode(values):
    return (f"{len(values)}\n" + " ".join(map(str, values)) + "\n").encode("ascii")


def oracle(values):
    # A strict inversion contributes once, at the highest bit where the ranks differ.
    # For that bit, the two ranks share their prefix and the earlier rank has a one.
    highest = max(values, default=0)
    count = 0
    for bit in range(highest.bit_length()):
        ones = [0] * ((highest >> (bit + 1)) + 1)
        mask = 1 << bit
        for rank in values:
            prefix = rank >> (bit + 1)
            if rank & mask:
                ones[prefix] += 1
            else:
                count += ones[prefix]
    return f"{count}\n".encode("ascii")
