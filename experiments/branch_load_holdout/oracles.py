import math
from pathlib import Path

root = Path(__file__).resolve().parent
def args(folder):
    return map(int, (root / folder / 'in').read_text().split())
def oracle(folder, value):
    (root / folder / 'oracle').write_text(f'{value}\n')

pairs, state = args('binary-gcd')
def next_int():
    global state
    state = (1664525 * state + 1013904223) % (2**32)
    return state
oracle('binary-gcd', sum(math.gcd(next_int(), next_int()) for _ in range(pairs)))

size, points = args('stream-polynomial')
p = 1000000007
values = []
for j in range(points):
    x = 3 + 37*j
    xn = pow(x, size, p)
    inv = pow(x - 1, -1, p)
    s0 = (xn - 1) * inv % p
    s1 = (size * xn * (x-1) - x * (xn-1)) * inv**2 % p
    values.append(((17*(size-1)+22)*s0 - 17*s1) % p)
oracle('stream-polynomial', sum(values))

nodes, queries, seed = args('linked-rank')
mask = nodes - 1
def label(i):
    x = (i + 173531) % nodes
    for shift, odd in [(7, 107711), (9, 69353)]:
        x = ((x ^ (x >> shift)) * odd) % nodes
    return x ^ (x >> 5)
assert len({label(i) for i in range(nodes)}) == nodes
total = 0
for q in range(queries):
    seed = (seed * 1103515245 + 12345) % (2**32)
    total += label((seed + 1024 + q%512) % nodes)
oracle('linked-rank', total)
