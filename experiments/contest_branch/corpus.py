"""Matched point-update orders for the IOI 2014 Wall sample-grader executable."""
from random import Random

PROGRAM = "wall"
SIZE = 1 << 17
NAMES = ("ascending", "shuffled", "tiles32", "tiles4096", "bit_reversed")


def make(name, size=SIZE):
    """Return the query stream and the final wall in left-to-right order."""
    if name not in NAMES:
        raise ValueError(f"unknown Wall order: {name}")
    if size < 1 or size > 500_000 or size & (size - 1):
        raise ValueError("Wall size must be a power of two within the update limit")

    positions = list(range(size))
    rng = Random(20140717)
    if name == "shuffled":
        rng.shuffle(positions)
    elif name in ("tiles32", "tiles4096"):
        width = 32 if name == "tiles32" else 4096
        for start in range(0, size, width):
            tile = positions[start:start + width]
            rng.shuffle(tile)
            positions[start:start + width] = tile
    elif name == "bit_reversed":
        depth = size.bit_length() - 1
        positions = [int(f"{pos:0{depth}b}"[::-1], 2) for pos in positions]

    # Each position starts at zero and receives exactly one raise, so its
    # final height needs no tree traversal and is independent of the order.
    heights = [10_000 + pos * 3607 % 90_000 for pos in range(size)]
    lines = [f"{size} {size}"]
    lines.extend(f"1 {pos} {pos} {heights[pos]}" for pos in positions)
    return "\n".join(lines) + "\n", "\n".join(map(str, heights)) + "\n"
