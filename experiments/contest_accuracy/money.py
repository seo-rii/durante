"""Generated markets for ICPC World Finals 2017, Money for Nothing (problem D)."""
from bisect import bisect_left
from random import Random

CASES = ("mixed_frontiers", "dense_scrambled", "mostly_dominated", "narrow_windows")


def market(name, size):
    """Return contest-format offers and the independent piecewise-quadratic oracle."""
    # Each market has the same number of offers per side. The ratios control
    # how much work remains after dominated offers are removed.
    stride, slopes, seed = {
        "mixed_frontiers": (2, (1, 3, 1), 201701),
        "dense_scrambled": (1, (3, 1, 2), 201702),
        "mostly_dominated": (64, (2, 3, 1), 201703),
        "narrow_windows": (1, (1, 3, 2), 201704),
    }[name]
    rng = Random(seed)
    core = max(3, size // stride)
    consumers = []
    segments = []
    bid, until = 10_000, 240_000
    for j, slope in enumerate(slopes):
        intercept = until + slope * bid
        prices = []
        for _ in range(j * core // 3, (j + 1) * core // 3):
            prices.append(bid)
            consumers.append((bid, until))
            gap = rng.randrange(1, 5)
            bid += gap
            until -= gap * slope
        segments.append((prices, intercept, slope))

    if name == "narrow_windows":
        producers = [(bid - 9, until - 12) for bid, until in consumers]
    else:
        producers = [(bid - 7000, until - 30_000) for bid, until in consumers]
    for _ in range(core, size):
        price, day = producers[rng.randrange(core)]
        producers.append((price + rng.randrange(1, 2000), day + rng.randrange(1, 2000)))
        bid, until = consumers[rng.randrange(core)]
        consumers.append((bid - rng.randrange(1, 2000), until - rng.randrange(1, 2000)))

    # On one segment the positive profit is (q-p)*(intercept-slope*q-d).
    # Its maximum is at the two available bids around the parabola vertex.
    # Added consumers offer strictly worse terms than a point on a segment.
    best = 0
    for price, day in producers:
        for bids, intercept, slope in segments:
            denominator = 2 * slope
            vertex_ceiling = (intercept - day + slope * price + denominator - 1) // denominator
            index = bisect_left(bids, vertex_ceiling)
            for j in (index - 1, index):
                if 0 <= j < len(bids):
                    q = bids[j]
                    duration = intercept - slope * q - day
                    if q > price and duration > 0:
                        best = max(best, (q - price) * duration)

    rng.shuffle(producers)
    rng.shuffle(consumers)
    return producers, consumers, best


def make(name):
    producers, consumers, expected = market(name, 16_000)
    lines = [f"{len(producers)} {len(consumers)}"]
    lines.extend(f"{price} {day}" for offers in (producers, consumers) for price, day in offers)
    return "\n".join(lines) + "\n", f"{expected}\n"
