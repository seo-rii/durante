import sys

# CPython 3.11+ runs Python-to-Python calls without C recursion, so raising
# the interpreter limit is enough for a depth-n path. PyPy needs a big thread
# stack as well; that is the classic contest workaround applied below.
MOD = 1_000_000_007
sys.setrecursionlimit(3_000_000)


def main():
    data = sys.stdin.buffer.read().split()
    n = int(data[0])
    adj = [[] for _ in range(n + 1)]
    for i in range(1, 2 * n - 1, 2):
        u, v = int(data[i]), int(data[i + 1])
        adj[u].append(v)
        adj[v].append(u)
    state = [0, 0]  # max depth, total

    def dfs(v, parent, depth):
        if depth > state[0]:
            state[0] = depth
        size = 1
        for u in adj[v]:
            if u != parent:
                size += dfs(u, v, depth + 1)
        state[1] = (state[1] + size * v) % MOD
        return size

    dfs(1, 0, 0)
    print(state[0], state[1])


if sys.implementation.name == "pypy":
    import threading
    threading.stack_size(512 * 1024 * 1024)
    worker = threading.Thread(target=main)
    worker.start()
    worker.join()
else:
    main()
