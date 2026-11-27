"""Matched Scrivener orders that vary how far each stream advances within a page."""
from collections import OrderedDict
from itertools import zip_longest
from random import Random

from scrivener_order.corpus import SIZE
from scrivener_pages import corpus as pages

NAMES = ("ascending", "shuffled", "lanes32", "lanes24", "lanes64", "descending48",
         "tiles64_lanes48", "tiles512_lanes48", "bitrev_tiles512")


def _ranks(name, size):
    if name == "bitrev_tiles512":
        order = [0]
        for bit in reversed(range(9)):
            order += [rank + (1 << bit) for rank in order]
        for start in range(0, size, 512):
            yield from (start + rank for rank in order if start + rank < size)
        return
    lanes = {"lanes24": 24, "lanes64": 64, "descending48": 48,
             "tiles64_lanes48": 48, "tiles512_lanes48": 48}[name]
    runs = [list(range(i * size // lanes, (i + 1) * size // lanes)) for i in range(lanes)]
    if name == "descending48":
        for run in runs:
            run.reverse()
    elif name.startswith("tiles"):
        tile_size = 64 if name == "tiles64_lanes48" else 512
        rng = Random(20121128)
        for run in runs:
            for start in range(0, len(run), tile_size):
                tile = run[start:start + tile_size]
                rng.shuffle(tile)
                run[start:start + tile_size] = tile
    for column in zip_longest(*runs):
        yield from (rank for rank in column if rank is not None)


def make(name, size=SIZE):
    if name in ("ascending", "shuffled", "lanes32"):
        return pages.make(name, size)
    if name not in NAMES:
        raise ValueError(f"unknown Scrivener order {name}")
    text, answers = pages.make("ascending", size)
    lines = text.splitlines(keepends=True)
    queries = lines[size + 1:]
    # The ascending oracle looks up the typed character at each position.
    answer_lines = answers.splitlines(keepends=True)
    order = list(_ranks(name, size))
    return ("".join(lines[:size + 1]) + "".join(queries[i] for i in order),
            "".join(answer_lines[i] for i in order))


def features(text):
    """Count LRU misses that are first visits or return more than eight positions away."""
    counts = pages.features(text)
    # The existing parser validates commands and computes the three reference features.
    words = iter(text.split())
    next(words)
    recent = OrderedDict()
    last = {}
    disruptive = 0
    for command, value in zip(words, words, strict=True):
        if command == "P":
            position = int(value.lstrip("0") or "0")
            page = position // 512
            if page in recent:
                recent.move_to_end(page)
            else:
                disruptive += page not in last or abs(last[page] - position) > 8
                recent[page] = None
                if len(recent) > 16:
                    recent.popitem(last=False)
            last[page] = position
    return dict(accesses=counts["accesses"], misses=counts["misses"], disruptive=disruptive,
                position_bits=counts["position_bits"])
