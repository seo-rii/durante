"""Held-out Scrivener query orders and exact-output regression controls."""
from itertools import chain
from random import Random

from scrivener_lazy import corpus as lazy
from scrivener_order.corpus import SIZE
from scrivener_streams import corpus as streams

CONTROLS = ("bitrev_tiles256", "lanes80", "descending40", "tiles96_lanes40")
TILES = {
    "tiles192_lanes112": (192, 112),
    "tiles192_lanes144": (192, 144),
    "tiles160_lanes36": (160, 36),
    "tiles768_lanes52": (768, 52),
}
FRESH = ("adjacent_swaps", "shuffle_seven_eighths", *TILES, "bitrev_tiles128", "descending56")
NAMES = ("ascending", "shuffled", *CONTROLS, *FRESH)
SEED = 20121130


def make(name, size=SIZE):
    if name in ("ascending", "shuffled", *CONTROLS):
        return lazy.make(name, size)
    if name == "adjacent_swaps":
        ranks = (position ^ 1 if (position ^ 1) < size else position for position in range(size))
    elif name == "shuffle_seven_eighths":
        boundary = 7 * size // 8
        prefix = list(range(boundary))
        Random(SEED).shuffle(prefix)
        ranks = chain(prefix, range(boundary, size))
    elif name in TILES:
        width, lanes = TILES[name]
        ranks = streams.interleaved_ranks(size, lanes, descending=False, tile_size=width, seed=SEED)
    elif name == "bitrev_tiles128":
        ranks = streams.bit_reversed_tiles(size, 128)
    elif name == "descending56":
        ranks = streams.interleaved_ranks(size, 56, descending=True)
    else:
        raise ValueError(f"unknown Scrivener order {name}")
    return streams.reorder_queries(size, ranks)


def features(text):
    return streams.features(text, revisit_metrics=True)
