"""Deterministic contest-style inputs and independent reference answers.

Each problem has a generator and a reference solver that does not share code
or algorithm structure with the submissions under tests/contest/<problem>/.
Two size tiers exist: "full" for compiled and JIT runtimes and "lite" for
interpreters, so every cell of the matrix runs in seconds rather than minutes.
"""
import bisect
from collections import deque
import heapq
import random

MOD = 1_000_000_007

# (full, lite) parameters per problem.
SIZES = {
    "bfs_grid": ({"h": 2000, "w": 2000}, {"h": 1200, "w": 1200}),
    "dijkstra": ({"n": 200_000, "m": 600_000}, {"n": 100_000, "m": 300_000}),
    "lcs": ({"n": 8000, "m": 8000}, {"n": 2600, "m": 2600}),
    "fenwick": ({"n": 700_000, "q": 700_000}, {"n": 180_000, "q": 180_000}),
    "kmp": ({"l": 20_000_000, "p": 24}, {"l": 3_000_000, "p": 24}),
    "sort_bs": ({"n": 500_000, "q": 500_000}, {"n": 300_000, "q": 300_000}),
    "deep_dfs": ({"n": 1_000_000}, {"n": 400_000}),
}
PROBLEMS = tuple(SIZES)


def _rng(problem, tier):
    return random.Random(f"durante-contest:{problem}:{tier}:v1")


def gen_bfs_grid(rng, h, w):
    raw = rng.randbytes(h * w)
    # About 30% walls: above the site-percolation threshold, so a long path exists.
    table = bytes(35 if b < 77 else 46 for b in range(256))
    cells = bytearray(raw.translate(table))
    # Clear 6x6 corners so neither endpoint sits in a small enclosed pocket.
    for r in range(6):
        for c in range(6):
            cells[r * w + c] = cells[(h - 1 - r) * w + (w - 1 - c)] = 46
    rows = [bytes(cells[r * w:(r + 1) * w]) for r in range(h)]
    return b"%d %d\n" % (h, w) + b"\n".join(rows) + b"\n"


def ref_bfs_grid(data):
    lines = data.split(b"\n")
    h, w = map(int, lines[0].split())
    grid = b"".join(lines[1:1 + h])
    dist = [-1] * (h * w)
    if grid[0] == 46:
        dist[0] = 0
    frontier = deque([0] if grid[0] == 46 else [])
    reach = len(frontier)
    while frontier:
        cell = frontier.popleft()
        r, c = divmod(cell, w)
        nxt = dist[cell] + 1
        for ok, other in ((r > 0, cell - w), (r < h - 1, cell + w), (c > 0, cell - 1), (c < w - 1, cell + 1)):
            if ok and grid[other] == 46 and dist[other] < 0:
                dist[other] = nxt
                reach += 1
                frontier.append(other)
    return f"{dist[-1]} {reach}\n"


def gen_dijkstra(rng, n, m):
    out = [b"%d %d" % (n, m)]
    for _ in range(m):
        out.append(b"%d %d %d" % (rng.randrange(1, n + 1), rng.randrange(1, n + 1), rng.randrange(1, 1_000_001)))
    return b"\n".join(out) + b"\n"


def ref_dijkstra(data):
    it = iter(data.split())
    n, m = int(next(it)), int(next(it))
    adj = [[] for _ in range(n + 1)]
    for _ in range(m):
        u, v, w = int(next(it)), int(next(it)), int(next(it))
        adj[u].append((w, v))
    best = {1: 0}
    done = set()
    heap = [(0, 1)]
    while heap:
        d, u = heapq.heappop(heap)
        if u in done:
            continue
        done.add(u)
        for w, v in adj[u]:
            nd = d + w
            if nd < best.get(v, 1 << 62):
                best[v] = nd
                heapq.heappush(heap, (nd, v))
    return f"{best.get(n, -1)} {len(done)} {sum(best.values())}\n"


def gen_lcs(rng, n, m):
    return ("".join(rng.choice("acgt") for _ in range(n)) + "\n"
            + "".join(rng.choice("acgt") for _ in range(m)) + "\n").encode()


def ref_lcs(data):
    a, b = data.split()
    # Allison-Dix bit-parallel LCS, independent of the quadratic submissions.
    masks = {}
    for i, ch in enumerate(a):
        masks[ch] = masks.get(ch, 0) | (1 << i)
    full = (1 << len(a)) - 1
    s = full
    for ch in b:
        u = s & masks.get(ch, 0)
        s = ((s + u) | (s - u)) & full
    return f"{len(a) - bin(s).count('1')}\n"


def gen_fenwick(rng, n, q):
    out = [b"%d %d" % (n, q), b" ".join(b"%d" % rng.randrange(0, 1_000_000_001) for _ in range(n))]
    for _ in range(q):
        if rng.random() < 0.5:
            out.append(b"1 %d %d" % (rng.randrange(1, n + 1), rng.randrange(0, 1_000_000_001)))
        else:
            left, right = sorted((rng.randrange(1, n + 1), rng.randrange(1, n + 1)))
            out.append(b"2 %d %d" % (left, right))
    return b"\n".join(out) + b"\n"


def ref_fenwick(data):
    it = iter(data.split())
    n, q = int(next(it)), int(next(it))
    size = 1
    while size < n:
        size *= 2
    tree = [0] * (2 * size)
    for i in range(n):
        tree[size + i] = int(next(it))
    for i in range(size - 1, 0, -1):
        tree[i] = tree[2 * i] + tree[2 * i + 1]
    out = []
    for _ in range(q):
        kind, x, y = int(next(it)), int(next(it)), int(next(it))
        if kind == 1:
            i = size + x - 1
            while i:
                tree[i] += y
                i //= 2
        else:
            lo, hi, total = size + x - 1, size + y, 0
            while lo < hi:
                if lo & 1:
                    total += tree[lo]
                    lo += 1
                if hi & 1:
                    hi -= 1
                    total += tree[hi]
                lo //= 2
                hi //= 2
            out.append(str(total))
    return "\n".join(out) + "\n"


def gen_kmp(rng, l, p):
    # A nearly periodic text with sparse mutations makes the failure function
    # fall back often and keeps overlapping matches frequent.
    unit = "aab"
    pattern = (unit * (p // len(unit) + 1))[:p - 1] + "b"
    text = bytearray((unit * (l // len(unit) + 1))[:l].encode())
    for _ in range(l // 40):
        text[rng.randrange(l)] = 98 if rng.random() < 0.5 else 97
    return bytes(text) + b"\n" + pattern.encode() + b"\n"


def ref_kmp(data):
    text, pattern = data.split()
    count = total = 0
    at = text.find(pattern)
    while at >= 0:
        count += 1
        total += at + 1
        at = text.find(pattern, at + 1)
    return f"{count} {total}\n"


def gen_sort_bs(rng, n, q):
    values = b" ".join(b"%d" % rng.randrange(-1_000_000_000, 1_000_000_001) for _ in range(n))
    queries = b" ".join(b"%d" % rng.randrange(-1_000_000_000, 1_000_000_001) for _ in range(q))
    return b"%d %d\n" % (n, q) + values + b"\n" + queries + b"\n"


def ref_sort_bs(data):
    it = data.split()
    n, q = int(it[0]), int(it[1])
    values = sorted(map(int, it[2:2 + n]))
    return "".join(f"{bisect.bisect_right(values, int(x))}\n" for x in it[2 + n:2 + n + q])


def gen_deep_dfs(rng, n):
    order = list(range(2, n + 1))
    rng.shuffle(order)
    order.insert(0, 1)  # Root 1 is one end of the path: recursion depth n.
    edges = [(order[i], order[i + 1]) if rng.random() < 0.5 else (order[i + 1], order[i]) for i in range(n - 1)]
    rng.shuffle(edges)
    return b"%d\n" % n + b"".join(b"%d %d\n" % edge for edge in edges)


def ref_deep_dfs(data):
    it = data.split()
    n = int(it[0])
    nxt = {}
    for i in range(1, 2 * n - 1, 2):
        u, v = int(it[i]), int(it[i + 1])
        nxt.setdefault(u, []).append(v)
        nxt.setdefault(v, []).append(u)
    # Walk the path from root 1; node at position i has subtree size n - i.
    total, prev, cur = 0, 0, 1
    for i in range(n):
        total = (total + cur * (n - i)) % MOD
        following = [v for v in nxt.get(cur, ()) if v != prev]
        prev, cur = cur, (following[0] if following else 0)
    return f"{n - 1} {total}\n"


def generate(problem, tier):
    params = SIZES[problem][0 if tier == "full" else 1]
    return globals()["gen_" + problem](_rng(problem, tier), **params)


def reference(problem, data):
    return globals()["ref_" + problem](data)
