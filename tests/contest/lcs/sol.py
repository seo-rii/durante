import sys


def main():
    a, b = sys.stdin.buffer.read().split()
    m = len(b)
    prev = [0] * (m + 1)
    for ca in a:
        cur = [0] * (m + 1)
        for j in range(1, m + 1):
            if ca == b[j - 1]:
                cur[j] = prev[j - 1] + 1
            else:
                x, y = prev[j], cur[j - 1]
                cur[j] = x if x > y else y
        prev = cur
    print(prev[m])


main()
