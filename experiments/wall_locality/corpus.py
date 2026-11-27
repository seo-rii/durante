"""Permutations of overlapping IOI 2014 Wall range raises."""
from random import Random

PROGRAM = "wall"
SIZE = 1 << 17
WIDTH = 8
ANCHORS = ("ascending", "shuffled")
NAMES = ANCHORS + ("tiles128", "tiles8192", "stride64", "page_round_robin", "shuffled_tiles")


def make(name, size=SIZE):
    """Return sample-grader input and independently computed final heights."""
    if name not in NAMES:
        raise ValueError(f"unknown Wall order: {name}")
    count = size - WIDTH + 1
    if count < 1 or count > 500_000:
        raise ValueError("Wall size must allow between 1 and 500,000 width-eight raises")

    starts = list(range(count))
    rng = Random(20140718)
    if name == "shuffled":
        rng.shuffle(starts)
    elif name in ("tiles128", "tiles8192"):
        width = 128 if name == "tiles128" else 8192
        for i in range(0, count, width):
            tile = starts[i:i + width]
            rng.shuffle(tile)
            starts[i:i + width] = tile
    elif name in ("stride64", "page_round_robin"):
        stride = 64 if name == "stride64" else 512
        starts = [left for offset in range(stride) for left in range(offset, count, stride)]
    elif name == "shuffled_tiles":
        tiles = [starts[i:i + 256] for i in range(0, count, 256)]
        rng.shuffle(tiles)
        starts = [left for tile in tiles for left in tile]

    heights = [10_000 + (left * 35_617 + 7_921) % 90_000 for left in range(count)]
    commands = [f"{size} {count}"]
    commands.extend(f"1 {left} {left + WIDTH - 1} {heights[left]}" for left in starts)
    # For each cell, take the maximum over the (at most eight) raises covering it.
    wall = [max(heights[max(0, pos - WIDTH + 1):min(count, pos + 1)]) for pos in range(size)]
    return "\n".join(commands) + "\n", "\n".join(map(str, wall)) + "\n"
