"""Compare cache predictions on fresh permutations of the registered Scrivener queries."""
from io import StringIO
from itertools import pairwise
from random import Random

from scrivener_order import corpus as original

NAMES = ("ascending", "shuffled", "tiles32", "tiles4096", "reverse_tiles")
TILE_WIDTH = {"tiles32": 32, "tiles4096": 4096, "reverse_tiles": 256}


def make(name, size=original.SIZE):
    if name in ("ascending", "shuffled"):
        return original.matched_case(name, size)

    width = TILE_WIDTH[name]
    text, answers = original.matched_case("ascending", size)
    commands = text.splitlines(keepends=True)
    prefix = commands[:size + 1]
    # Reorder each complete query together with its known answer.
    queries = list(zip(commands[size + 1:], answers.splitlines(keepends=True), strict=True))
    blocks = [queries[i:i + width] for i in range(0, size, width)]
    if name == "reverse_tiles":
        blocks.reverse()
    else:
        rng = Random(20121209)
        for block in blocks:
            rng.shuffle(block)
    ordered = [query for block in blocks for query in block]
    return ("".join(prefix) + "".join(command for command, _ in ordered),
            "".join(answer for _, answer in ordered))


def position_bits(text):
    """Use the #98 order statistic on adjacent print operations."""
    positions = (int(line[2:]) for line in StringIO(text) if line.startswith("P "))
    return sum(abs(right - left).bit_length() for left, right in pairwise(positions))
