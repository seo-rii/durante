"""Paired DAGs with the same edges and distances but different decrease counts."""

CENTERS = 24
TARGETS = 6144
BASE = 80_000


def edges(order, centers=CENTERS, targets=TARGETS):
    if order not in ("once", "many") or centers <= 0 or targets <= 0:
        raise ValueError("expected a registered order and positive layer sizes")
    for j in range(centers):
        yield 1, j + 2, 10 + j
    for j in range(centers):
        extra = j if order == "once" else centers - 1 - j
        for k in range(targets):
            yield j + 2, centers + 2 + k, BASE + k + extra - (10 + j)


def encode(order, centers=CENTERS, targets=TARGETS):
    header = f"{1 + centers + targets} {centers * (targets + 1)}\n"
    return (header + "".join(f"{u} {v} {w}\n" for u, v, w in edges(order, centers, targets))).encode()


def answer(centers=CENTERS, targets=TARGETS):
    # All center distances are less than the distance to any target. Each
    # target has minimum incoming path length BASE+k on both schedules.
    total = sum(range(10, 10 + centers)) + sum(range(BASE, BASE + targets))
    return f"{BASE + targets - 1} {centers + targets + 1} {total}\n".encode()
