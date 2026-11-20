"""Compute independently expected stdout for the three memory-cost holdouts."""
from itertools import accumulate
from pathlib import Path

HERE = Path(__file__).resolve().parent
MASK = (1 << 32) - 1


def inputs(name):
    return map(int, (HERE / name / "in").read_text().split())


def words(state):
    while True:
        state ^= (state << 13) & MASK
        state ^= state >> 17
        state ^= (state << 5) & MASK
        yield state


def fenwick_prefix():
    size, phases, count, seed = inputs("fenwick-prefix")
    rng = words(seed)
    array = [0] * size
    total = 0
    for _ in range(phases):
        for _ in range(count):
            word = next(rng)
            array[word % size] += 1 + word // 2**27
        prefix = list(accumulate(array))
        total += sum(prefix[next(rng) % size] for _ in range(count))
    return str(total)


def kmp_match():
    blocks, seed = inputs("kmp-match")
    rng = words(seed)
    text = bytearray(b"ababababa") * blocks
    text[8::9] = bytes(b"aabc"[next(rng) // 2**30] for _ in range(blocks))
    pattern = b"ababababc"
    offset = count = position_sum = 0
    while (start := text.find(pattern, offset)) >= 0:
        position_sum += start + len(pattern)
        count += 1
        offset = start + 1
    return f"{count} {position_sum}"


def union_find():
    nodes, graphs, edges, queries, seed = inputs("union-find")
    rng = words(seed)
    hits = ordinal_sum = 0
    for graph in range(graphs):
        adjacency = [[] for _ in range(nodes)]
        for _ in range(edges):
            u, v = next(rng) % nodes, next(rng) % nodes
            adjacency[u].append(v)
            adjacency[v].append(u)
        component = [-1] * nodes
        for start in range(nodes):
            if component[start] == -1:
                component[start] = start
                stack = [start]
                while stack:
                    for v in adjacency[stack.pop()]:
                        if component[v] == -1:
                            component[v] = start
                            stack.append(v)
        for offset in range(1, queries + 1):
            a, b = next(rng) % nodes, next(rng) % nodes
            if component[a] == component[b]:
                hits += 1
                ordinal_sum += graph * queries + offset
    return f"{hits} {ordinal_sum}"


if __name__ == "__main__":
    for case, oracle in [("fenwick-prefix", fenwick_prefix), ("kmp-match", kmp_match), ("union-find", union_find)]:
        print(case, oracle())
