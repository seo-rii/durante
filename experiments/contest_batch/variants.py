"""Fresh distributions for the second contest calibration study; supplied as ordinary grader text."""
from bisect import bisect_left
from random import Random

from contest_warm.dreaming import connected_diameter
from contest_warm.posterize import palette_error
from contest_warm.scrivener import answers


def forest(name):
    n, link, seed = {"comb_islands": (81_600, 1600, 13055),
                     "permuted_recursive": (95_000, 5150, 19052)}[name]
    rng = Random(seed)
    labels = list(range(n))
    edges = []
    if name == "comb_islands":
        # Each 600-vertex tree has a long spine, with two pendant teeth per spine vertex.
        for v in range(n):
            index = v % 600
            if index:
                root = v - index
                u = v - 1 if index < 200 else root + (index - 200) % 200
                edges.append((u, v, 1 + rng.randrange(1500)))
    else:
        rng.shuffle(labels)
        first = 0
        while first < n:
            stop = min(n, first + rng.randint(32, 4096))
            for v in range(first + 1, stop):
                u = rng.randrange(first, v)
                edges.append((labels[u], labels[v], 1 + rng.randrange(10_000)))
            first = stop
        rng.shuffle(edges)
    diameter = connected_diameter(n, edges, link)
    text = f"{n} {len(edges)} {link}\n" + "".join(f"{a} {b} {w}\n" for a,b,w in edges)
    return text, f"{diameter}\n"


def histogram(name):
    if name == "two_bands":
        k = 12
        values = [r for r in range(256) if r < 103 or r >= 153]
        h = [(r, 300 + (127 - abs(r-128))**2 * (1 + r % 13)) for r in values]
    elif name == "log_weights":
        k = 121
        rng = Random(1118)
        h = [(r, 1 << rng.randrange(27)) for r in sorted(rng.sample(range(256), 247))]
    else:
        raise KeyError(name)
    text = f"{len(h)} {k}\n" + "".join(f"{v} {p}\n" for v,p in h)
    return text, f"{palette_error(h, k)}\n"


def editor(name):
    rng = Random({"prefix_forks": 1112, "type_then_scan": 1217}[name])
    ops = []
    if name == "prefix_forks":
        prefix_len = 44_000
        ops.extend(("T", rng.choice("abcde")) for _ in range(prefix_len))
        for _ in range(650):
            size = rng.randrange(40, 155)
            for i in range(size):
                ops.append(("T", rng.choice("abcdef")))
                if i % 2 == 0:
                    ops.append(("P", rng.randrange(prefix_len+i+1)))
            ops.append(("U", size))
    else:
        prefix_len = 100_000
        ops.extend(("T", rng.choice("abcdefghijklmnopqrstuvwxyz")) for _ in range(prefix_len))
        for i in range(88_000):
            pos = (i * 7919) % prefix_len
            ops.append(("P", pos))
    text = f"{len(ops)}\n" + "".join(f"{op} {arg}\n" for op,arg in ops)
    return text, answers(ops)


def wall(name):
    rng = Random({"drifting_bands": 7821, "checker_tiles": 1485}[name])
    n, count = 20_480, 32_000
    lines = [f"{n} {count}"]
    heights = bytearray(n)
    tables = [[bytes(fn(a, b) for a in range(256)) for b in range(256)] for fn in (max, min)]
    for i in range(count):
        if name == "drifting_bands":
            width = n // 7 + i % 701
            left = (17 * i + i // 31) % (n - width + 1)
        else:
            width = 64 * (1 + (i // 71) % 9)
            left = min(n - width, ((i % 71) * 113 % (n // 64)) * 64)
        right = left + width - 1
        op, height = 1 + rng.randrange(2), rng.randrange(256)
        lines.append(f"{op} {left} {right} {height}")
        heights[left:right + 1] = heights[left:right + 1].translate(tables[op-1][height])
    return "\n".join(lines) + "\n", "\n".join(map(str,heights)) + "\n"


def market(name):
    rng = Random({"parallel_markets": 33481, "repeated_bids": 13413}[name])
    if name == "parallel_markets":
        prices = sorted(rng.sample(range(30_000,175_000), 23_000))
        buyers = [(q, 270_000 - q) for q in prices]
        sellers = [(p, 214_000 - p + rng.randrange(251)) for p in rng.sample(range(25_000, 158_000),18_000)]
        # For one seller profit is the concave quadratic (q-p)*(270000-q-d).
        # Scan the two available bids adjacent to its vertex.
        best = 0
        for p,d in sellers:
            index = bisect_left(prices, (270_000 - d + p + 1) // 2)
            for j in (index - 1, index):
                if 0 <= j < len(prices):
                    q = prices[j]
                    best = max(best, max(q-p, 0)*max(270_000-q-d, 0))
    else:
        bids = [50_000 + i*1024 for i in range(23)]
        buyers = [(q, 150_000-q+rng.randrange(4001)) for q in rng.choices(bids, k=30_000)]
        sellers = [(rng.randint(29_000,61_000),rng.randint(69_000,104_000)) for _ in range(22_000)]
        # All buyers of a given price are dominated by its latest end date.
        latest = {}
        for q,e in buyers:
            latest[q] = max(latest.get(q,0),e)
        best = max(max(q-p,0)*max(e-d,0) for p,d in sellers for q,e in latest.items())
    rng.shuffle(buyers)
    rng.shuffle(sellers)
    text = f"{len(sellers)} {len(buyers)}\n" + "".join(f"{p} {d}\n" for group in (sellers,buyers) for p,d in group)
    return text, f"{best}\n"


CASES = {"dreaming": ("comb_islands", "permuted_recursive"),
         "posterize": ("two_bands", "log_weights"),
         "scrivener": ("prefix_forks", "type_then_scan"),
         "wall": ("drifting_bands", "checker_tiles"),
         "money": ("parallel_markets", "repeated_bids")}
GENERATE = {"dreaming": forest, "posterize": histogram, "scrivener": editor, "wall": wall, "money": market}
