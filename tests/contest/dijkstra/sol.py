import sys
from heapq import heappop, heappush


def main():
    data = sys.stdin.buffer.read().split()
    n, m = int(data[0]), int(data[1])
    adj = [[] for _ in range(n + 1)]
    for i in range(2, 2 + 3 * m, 3):
        adj[int(data[i])].append((int(data[i + 1]), int(data[i + 2])))
    inf = 1 << 62
    dist = [inf] * (n + 1)
    dist[1] = 0
    pq = [(0, 1)]
    reach = total = 0
    while pq:
        d, u = heappop(pq)
        if d != dist[u]:
            continue
        reach += 1
        total += d
        for v, w in adj[u]:
            nd = d + w
            if nd < dist[v]:
                dist[v] = nd
                heappush(pq, (nd, v))
    print(dist[n] if dist[n] < inf else -1, reach, total)


main()
