import sys


def main():
    data = sys.stdin.buffer.read().split()
    n, q = int(data[0]), int(data[1])
    bit = [0] * (n + 1)
    for i in range(1, n + 1):
        bit[i] += int(data[1 + i])
        j = i + (i & -i)
        if j <= n:
            bit[j] += bit[i]
    out = []
    p = 2 + n
    for _ in range(q):
        t, x, y = data[p], int(data[p + 1]), int(data[p + 2])
        p += 3
        if t == b"1":
            while x <= n:
                bit[x] += y
                x += x & -x
        else:
            s = 0
            while y > 0:
                s += bit[y]
                y -= y & -y
            x -= 1
            while x > 0:
                s -= bit[x]
                x -= x & -x
            out.append(s)
    sys.stdout.write("\n".join(map(str, out)) + "\n")


main()
