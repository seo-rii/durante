"""Original generated tests for IOI 2014 Wall. See https://ioinformatics.org/files/ioi2014problem2.pdf."""
from random import Random

CASES = ("aligned_tiles", "crossed_intervals", "scattered_points", "nested_ranges")


def make(name):
    """Return the sample-grader input and final heights after applying every phase to a flat array."""
    if name not in CASES:
        raise ValueError(f"unknown Wall case {name}")
    n, k = 16384, 25000
    rng = Random(20140713 + CASES.index(name))
    lines = [f"{n} {k}"]
    heights = bytearray(n)
    # These inputs use heights 0..255. Apply the scalar min/max to each affected
    # column in C via bytearray.translate, independently of lazy tree propagation.
    lower = [bytes(max(h, v) for v in range(256)) for h in range(256)]
    upper = [bytes(min(h, v) for v in range(256)) for h in range(256)]
    for i in range(k):
        if name == "aligned_tiles":
            width = 1 << rng.randrange(2, 12)
            left = rng.randrange(n // width) * width
            right = left + width - 1
        elif name == "crossed_intervals":
            left, right = sorted((rng.randrange(n), rng.randrange(n)))
        elif name == "scattered_points":
            left = rng.randrange(n)
            right = min(n - 1, left + rng.randrange(8))
        else:
            # Each 256-update batch first shrinks towards a pivot then expands.
            pivot = n // 4 + ((i // 256) * 2357 % (n // 2))
            radius = abs(i % 256 - 128) * 64
            left, right = max(0, pivot - radius), min(n - 1, pivot + radius)
        op = 1 + rng.randrange(2)
        h = rng.randrange(256)
        lines.append(f"{op} {left} {right} {h}")
        table = lower[h] if op == 1 else upper[h]
        heights[left:right + 1] = heights[left:right + 1].translate(table)
    return "\n".join(lines) + "\n", "\n".join(map(str, heights)) + "\n"
