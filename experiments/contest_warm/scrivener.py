"""Generate the text-grader stream and answer queries offline by traversing the tree of typed letters."""
import random

CASES = ("deep_random_queries", "undo_branches", "recent_queries", "distant_versions")


def answers(operations):
    children = [[]]
    queries = [[]]
    history = [0]
    output = []
    for op, argument in operations:
        if op == "T":
            child = len(children)
            children[history[-1]].append((child, argument))
            children.append([])
            queries.append([])
            history.append(child)
        elif op == "U":
            history.append(history[-1 - argument])
        else:
            queries[history[-1]].append((len(output), argument))
            output.append(None)
    path = []
    pending = [(0, "")]
    while pending:
        node, letter = pending.pop()
        if node == -1:
            path.pop()
            continue
        if node:
            path.append(letter)
            pending.append((-1, ""))
        for index, position in queries[node]:
            output[index] = path[position]
        pending.extend(children[node])
    return "".join(ch + "\n" for ch in output)


def make(name):
    count, query_pct, undo_pct = {
        "deep_random_queries": (160_000, 36, 0),
        "undo_branches": (105_000, 23, 29),
        "recent_queries": (260_000, 42, 1),
        "distant_versions": (190_000, 25, 19),
    }[name]
    rng = random.Random(91329 + CASES.index(name))
    lengths = [0]
    operations = []
    for _ in range(count):
        kind = rng.randrange(100)
        depth = lengths[-1]
        if depth and kind < query_pct:
            first = max(0, depth - 12) if name == "recent_queries" else 0
            operations.append(("P", rng.randrange(first, depth)))
        elif len(lengths) > 1 and kind < query_pct + undo_pct and kind >= query_pct:
            available = len(lengths) - 1
            span = available if name == "distant_versions" else min(available, 24)
            undo = rng.randint(1, span)
            lengths.append(lengths[-1 - undo])
            operations.append(("U", undo))
        else:
            operations.append(("T", chr(ord("a") + rng.randrange(26))))
            lengths.append(depth + 1)
    stdin = f"{count}\n" + "".join(f"{op} {arg}\n" for op, arg in operations)
    return stdin, answers(operations)
