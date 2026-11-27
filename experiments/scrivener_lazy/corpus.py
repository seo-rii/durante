"""Fresh interleaved print orders for the bounded-deadline experiment."""
from scrivener_order.corpus import SIZE
from scrivener_streams import corpus as streams

NAMES = ("ascending", "shuffled", "lanes20", "lanes80", "descending40",
         "tiles96_lanes40", "tiles384_lanes40", "bitrev_tiles256")
features = streams.features


def make(name, size=SIZE):
    if name in ("ascending", "shuffled"):
        return streams.make(name, size)
    if name == "bitrev_tiles256":
        return streams.reorder_queries(size, streams.bit_reversed_tiles(size, 256))
    lanes = {"lanes20": 20, "lanes80": 80, "descending40": 40,
             "tiles96_lanes40": 40, "tiles384_lanes40": 40}[name]
    width = {"tiles96_lanes40": 96, "tiles384_lanes40": 384}.get(name, 0)
    ranks = streams.interleaved_ranks(size, lanes, descending=name == "descending40", tile_size=width, seed=20121129)
    return streams.reorder_queries(size, ranks)
