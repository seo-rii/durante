import sys


def main():
    t, p = sys.stdin.buffer.read().split()
    m = len(p)
    fail = [0] * m
    k = 0
    for i in range(1, m):
        while k and p[i] != p[k]:
            k = fail[k - 1]
        if p[i] == p[k]:
            k += 1
        fail[i] = k
    count = total = k = 0
    for i, ch in enumerate(t):
        while k and ch != p[k]:
            k = fail[k - 1]
        if ch == p[k]:
            k += 1
        if k == m:
            count += 1
            total += i - m + 2
            k = fail[k - 1]
    print(count, total)


main()
