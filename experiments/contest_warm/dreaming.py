"""Generated weighted forests for IOI 2013 Dreaming; all new trails have the same length."""
from random import Random

CASES = ("long_paths", "star_groves", "balanced_scrambled", "fragmented")


def connected_diameter(n, edges, length):
    """Reroot each component to find a center, explicitly join centers, and measure the finished tree."""
    adj = [[] for _ in range(n)]
    for u, v, weight in edges:
        adj[u].append((v, weight))
        adj[v].append((u, weight))

    parent = [-1] * n
    down = [0] * n
    up = [0] * n
    centers = []
    for root in range(n):
        if parent[root] != -1:
            continue
        order = [root]
        parent[root] = root
        for v in order:
            for u, weight in adj[v]:
                if u != parent[v]:
                    parent[u] = v
                    order.append(u)
        for v in reversed(order):
            down[v] = max((down[u] + w for u, w in adj[v] if u != parent[v]), default=0)
        for v in order:
            first = second = 0
            best_child = -1
            for u, w in adj[v]:
                if u == parent[v]:
                    continue
                branch = w + down[u]
                if branch > first:
                    second, first, best_child = first, branch, u
                elif branch > second:
                    second = branch
            for u, w in adj[v]:
                if u != parent[v]:
                    up[u] = w + max(up[v], second if u == best_child else first)
        radius, center = min((max(down[v], up[v]), v) for v in order)
        centers.append((radius, center))

    hub = max(centers)[1]
    for _, center in centers:
        if center != hub:
            adj[center].append((hub, length))
            adj[hub].append((center, length))
    order = [hub]
    parent[hub] = hub
    for v in order:
        for u, _ in adj[v]:
            if u != parent[v]:
                parent[u] = v
                order.append(u)
    diameter = 0
    for v in reversed(order):
        longest = 0
        for u, w in adj[v]:
            if u != parent[v]:
                branch = w + down[u]
                diameter = max(diameter, longest + branch)
                longest = max(longest, branch)
        down[v] = longest
    return diameter


def make(name):
    n, size, length, seed = {
        "long_paths": (90_000, 31_001, 169, 813),
        "star_groves": (74_120, 137, 3100, 701),
        "balanced_scrambled": (100_000, 2039, 127, 812),
        "fragmented": (62_500, 7, 8900, 1601),
    }[name]
    rng = Random(seed)
    labels = list(range(n))
    if name == "balanced_scrambled":
        rng.shuffle(labels)
    edges = []
    for v in range(n):
        offset = v % size
        if offset == 0 or (name == "fragmented" and offset > (v // size) % size):
            continue
        root = v - offset
        if name == "star_groves":
            u = root
        elif name == "balanced_scrambled":
            u = root + (offset - 1) // 2
        else:
            u = v - 1
        edges.append((labels[u], labels[v], 1 + rng.randrange(10_000)))
    if name in ("balanced_scrambled", "star_groves"):
        rng.shuffle(edges)
    answer = connected_diameter(n, edges, length)
    rows = [f"{n} {len(edges)} {length}"]
    rows.extend(f"{a} {b} {w}" for a, b, w in edges)
    return "\n".join(rows) + "\n", f"{answer}\n"
