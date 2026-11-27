"""Matched Scrivener orders that vary how far each stream advances within a page."""
from collections import OrderedDict
from itertools import zip_longest
from random import Random

from scrivener_order.corpus import SIZE
from scrivener_pages import corpus as pages

NAMES = ("ascending", "shuffled", "lanes32", "lanes24", "lanes64", "descending48",
         "tiles64_lanes48", "tiles512_lanes48", "bitrev_tiles512")


def bit_reversed_tiles(size, width):
    if width < 1 or width & (width - 1):
        raise ValueError("tile width must be a power of two")
    order = [0]
    for bit in reversed(range(width.bit_length() - 1)):
        order += [rank + (1 << bit) for rank in order]
    for start in range(0, size, width):
        yield from (start + rank for rank in order if start + rank < size)


def interleaved_ranks(size, lanes, *, descending=False, tile_size=0, seed=20121128):
    runs = [list(range(i * size // lanes, (i + 1) * size // lanes)) for i in range(lanes)]
    if descending:
        for run in runs:
            run.reverse()
    if tile_size:
        rng = Random(seed)
        for run in runs:
            for start in range(0, len(run), tile_size):
                tile = run[start:start + tile_size]
                rng.shuffle(tile)
                run[start:start + tile_size] = tile
    for column in zip_longest(*runs):
        yield from (rank for rank in column if rank is not None)


def _ranks(name, size):
    if name == "bitrev_tiles512":
        return bit_reversed_tiles(size, 512)
    lanes = {"lanes24": 24, "lanes64": 64, "descending48": 48,
             "tiles64_lanes48": 48, "tiles512_lanes48": 48}[name]
    tile_size = {"tiles64_lanes48": 64, "tiles512_lanes48": 512}.get(name, 0)
    return interleaved_ranks(size, lanes, descending=name == "descending48", tile_size=tile_size)


def reorder_queries(size, ranks):
    text, answers = pages.make("ascending", size)
    lines = text.splitlines(keepends=True)
    queries = lines[size + 1:]
    # Reorder the character-lookup oracle and the query referring to it together.
    answer_lines = answers.splitlines(keepends=True)
    order = list(ranks)
    return ("".join(lines[:size + 1]) + "".join(queries[i] for i in order),
            "".join(answer_lines[i] for i in order))


def make(name, size=SIZE):
    if name in ("ascending", "shuffled", "lanes32"):
        return pages.make(name, size)
    if name not in NAMES:
        raise ValueError(f"unknown Scrivener order {name}")
    return reorder_queries(size, _ranks(name, size))


def features(text, *, revisit_metrics=False):
    """Count LRU misses that are first visits or return more than eight positions away."""
    counts = pages.features(text)
    # The existing parser validates commands and computes the three reference features.
    words = iter(text.split())
    next(words)
    recent = OrderedDict()
    last = {}
    disruptive = 0
    last_visit = {}
    query = displacement = stale_disruptive = 0
    for command, value in zip(words, words, strict=True):
        if command == "P":
            position = int(value.lstrip("0") or "0")
            page = position // 512
            if page in recent:
                recent.move_to_end(page)
            else:
                delta = None if page not in last else abs(last[page] - position)
                if delta is None or delta > 8:
                    disruptive += 1
                    if revisit_metrics:
                        displacement += 0 if delta is None else delta
                        stale_disruptive += delta is None or query - last_visit[page] - 1 >= 128
                recent[page] = None
                if len(recent) > 16:
                    recent.popitem(last=False)
            last[page] = position
            if revisit_metrics:
                last_visit[page] = query
                query += 1
    result = dict(accesses=counts["accesses"], misses=counts["misses"], disruptive=disruptive,
                  position_bits=counts["position_bits"])
    if revisit_metrics:
        result.update(displacement=displacement, stale_disruptive=stale_disruptive)
    return result
