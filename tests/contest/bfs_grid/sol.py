import sys
from collections import deque


def main():
    data = sys.stdin.buffer.read().split()
    h, w = int(data[0]), int(data[1])
    grid = b"".join(data[2:2 + h])
    dist = [-1] * (h * w)
    q = deque()
    if grid[0] == 46:
        dist[0] = 0
        q.append(0)
    seen = len(q)
    while q:
        cur = q.popleft()
        d = dist[cur] + 1
        c = cur % w
        for nb in (cur - w, cur + w, cur - 1 if c else -1, cur + 1 if c + 1 < w else -1):
            if 0 <= nb < h * w and dist[nb] == -1 and grid[nb] == 46:
                dist[nb] = d
                seen += 1
                q.append(nb)
    print(dist[h * w - 1], seen)


main()
