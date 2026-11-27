import sys
from bisect import bisect_right


def main():
    data = sys.stdin.buffer.read().split()
    n, q = int(data[0]), int(data[1])
    a = sorted(map(int, data[2:2 + n]))
    sys.stdout.write("\n".join(str(bisect_right(a, int(x))) for x in data[2 + n:2 + n + q]) + "\n")


main()
