"""Larger text-grader inputs for Wall, Crayfish Scrivener and Money for Nothing."""
from heapq import heappop, heappush
from io import StringIO
from math import gcd


def envelope(intervals, columns, sign):
    """Sweep interval starts; the heap root is the strongest unexpired bound."""
    intervals.sort()
    pending = []
    start = 0
    for column in range(columns):
        while start < len(intervals) and intervals[start][0] == column:
            _, end, height = intervals[start]
            heappush(pending, (sign * height, end))
            start += 1
        while pending and pending[0][1] < column:
            heappop(pending)
        yield sign * pending[0][0] if pending else (0 if sign < 0 else 100_000)


def wall(name, columns=193_000, updates=324_000):
    text = StringIO()
    text.write(f"{columns} {updates}\n")
    if name == "phase_envelopes":
        raises, caps = [], []
        for i in range(updates):
            left = (104729 * i + (i // 251) * 17) % columns
            width = 1 + (97 * i % 480)
            right = min(columns - 1, left + width - 1)
            raising = i < updates // 2
            # Neighboring stretches have different ranges of construction heights.
            height = 10 + left // 4000 % 35 + (i * 7 % 48)
            text.write(f"{1 if raising else 2} {left} {right} {height}\n")
            (raises if raising else caps).append((left, right, height))
        heights = map(min, envelope(raises, columns, -1), envelope(caps, columns, 1))
    elif name == "scatter_repairs":
        heights = bytearray(columns)
        bounds = {op: [bytes(op(h, b) for h in range(256)) for b in range(100)] for op in (min, max)}
        for i in range(updates):
            # Sweep short repair spans through the whole wall, alternating operation and width.
            left = (65537 * i + 13 * (i // 43)) % columns
            stop = min(columns, left + 1 + (i * 11 % 29))
            lowering = (i * 13 % 19) < 8
            height = (31 * i + i // 73) % 100
            text.write(f"{2 if lowering else 1} {left} {stop - 1} {height}\n")
            heights[left:stop] = heights[left:stop].translate(bounds[min if lowering else max][height])
    else:
        raise KeyError(name)
    return text.getvalue(), "".join(f"{h}\n" for h in heights)


def letter(page, offset):
    return chr(97 + (page * 19 + offset * 11 + offset // 37) % 26)


def editor(name, pages=480_000, visits=214_000):
    text, output = StringIO(), StringIO()
    if name == "interwoven_pages":
        text.write(f"{2 * pages}\n")
        positions = 73017
        for end in range(pages):
            text.write(f"T {letter(0, end)}\n")
            # Read across the whole text while new versions are still being appended.
            positions = (1664525 * positions + 1013904223) % (1 << 32)
            pos = positions % (end + 1)
            text.write(f"P {pos}\n")
            output.write(letter(0, pos) + "\n")
    elif name == "bookmarked_chapters":
        # Every branch shares the same initial text. A branch's suffix is the sequence of
        # letters indexed by its own extension count, independent of its command history.
        prefix = pages // 2
        text.write(f"{prefix + 1 + 3 * visits + (visits + 2) // 3}\n")
        for pos in range(prefix):
            text.write(f"T {letter(0, pos)}\n")
        last_versions = [prefix] * 257
        lengths = [0] * len(last_versions)
        # The first bookmark restores the base version just before this edit.
        text.write("T z\n")
        commands = prefix + 1
        for i in range(visits):
            branch = (97 * i + i // 19) % len(last_versions)
            text.write(f"U {commands - last_versions[branch]}\nT {letter(branch + 1, lengths[branch])}\n")
            lengths[branch] += 1
            commands += 2
            last_versions[branch] = commands
            pos = (i * 7919 + branch * 151) % prefix
            text.write(f"P {pos}\n")
            output.write(letter(0, pos) + "\n")
            if i % 3 == 0:
                offset = (i // 3 * 37) % lengths[branch]
                pos, value = prefix + offset, letter(branch + 1, offset)
                text.write(f"P {pos}\n")
                output.write(value + "\n")
    else:
        raise KeyError(name)
    return text.getvalue(), output.getvalue()


def order(size, stride):
    # An affine permutation scrambles stream order without storing or sorting an oracle index.
    while gcd(size, stride) != 1:
        stride += 1
    for row in range(size):
        yield (row * stride + size // 3) % size


def market(name, size=400_000):
    """size offers on each side. The registered terraced size is a multiple of eight."""
    text = StringIO()
    text.write(f"{size} {size}\n")
    if name == "terraced_bids":
        if size % 8:
            raise ValueError("terraced market needs whole eight-offer groups")
        base_price, base_day = 1_000_000, 5_000_000
        # Only the highest bid at each end date survives. Those bids form a lattice
        # with q + e constant. For each seller check the lattice neighbors of the
        # unrestricted quadratic maximum, clipped to the available price range.
        first = base_price + 7
        last = base_price + size - 1
        total = base_price + base_day + 7
        best = 0
        for i in order(size, 15683):
            p = base_price - size // 3 + i
            d = base_day - size // 4 - i + (i % 11) // 3
            text.write(f"{p} {d}\n")
            near = (total - d + p - 2 * first) // 16
            for k in (near, near + 1):
                q = max(first, min(last, first + 8 * k))
                best = max(best, max(0, q-p) * max(0, total-q-d))
        for i in order(size, 26017):
            text.write(f"{base_price + i} {base_day - (i // 8) * 8}\n")
    elif name == "warehouse_lots":
        # Each warehouse has a preferred offer plus inferior offers on less favorable
        # dates and prices. Only warehouse representatives can improve the maximum.
        lots = min(size, 173)
        suppliers = [(2_000_000 + i * 2917, 4_000_000 - i * 3011) for i in range(lots)]
        consumers = [(2_175_000 + i * 2017, 4_215_000 - i * 2029) for i in range(lots)]
        for centers, direction, stride in ((suppliers, 1, 27191), (consumers, -1, 19543)):
            for i in order(size, stride):
                p, d = centers[i % lots]
                rank = i // lots
                dp = (rank * 131 + (rank // 7) * 19) % 44021
                dd = (rank * 107 + (rank // 11) * 17) % 44021
                text.write(f"{p + direction * dp} {d + direction * dd}\n")
        best = max(max(0, q-p) * max(0, e-d) for p,d in suppliers for q,e in consumers)
    else:
        raise KeyError(name)
    return text.getvalue(), f"{best}\n"


CASES = {"wall": ("phase_envelopes", "scatter_repairs"),
         "scrivener": ("interwoven_pages", "bookmarked_chapters"),
         "money": ("terraced_bids", "warehouse_lots")}
GENERATE = {"wall": wall, "scrivener": editor, "money": market}
