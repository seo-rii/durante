"""Generated weighted red histograms for ICPC World Finals 2017 Posterize."""
from random import Random

CASES = ("mild_ramp", "bright_peaks", "alternating", "broad_palette")


def palette_error(histogram, k):
    """Place increasing palette colors; charge each gap to its two nearest endpoints."""
    weights = [0] * 256
    for red, count in histogram:
        weights[red] = count
    # row[v][x] is the error if the entire prefix [0, x) uses palette color v.
    prefixes = []
    for v in range(256):
        row = [0]
        for red, count in enumerate(weights):
            row.append(row[-1] + count * (red - v) ** 2)
        prefixes.append(row)

    gap = []
    for b, right in enumerate(prefixes):
        costs = []
        for a in range(b):
            mid = (a + b) // 2 + 1
            left = prefixes[a]
            costs.append(left[mid] - left[a] + right[b] - right[mid])
        gap.append(costs)
    before = [row[v] for v, row in enumerate(prefixes)]
    for used in range(2, k + 1):
        after = [0] * 256
        for b in range(used - 1, 256):
            costs = gap[b]
            after[b] = min(before[a] + costs[a] for a in range(used - 2, b))
        before = after
    return min(before[b] + prefixes[b][256] - prefixes[b][b] for b in range(k - 1, 256))


def make(name):
    if name == "mild_ramp":
        k = 4
        histogram = [(red, 1000 + red * 33) for red in range(165)]
    elif name == "bright_peaks":
        k = 25
        rng = Random(2606)
        histogram = [(red, (2**26 - red if red % 29 == 0 else rng.randint(1, 4000)))
                     for red in sorted(rng.sample(range(256), 221))]
    elif name == "alternating":
        k = 83
        histogram = [(red, (2**26 - red**2) if red % 2 else 1 + red) for red in range(256)]
    elif name == "broad_palette":
        k = 157
        rng = Random(2921)
        histogram = [(red, rng.randint(1, 2**26)) for red in sorted(rng.sample(range(256), 243))]
    else:
        raise KeyError(name)
    rows = [f"{len(histogram)} {k}"]
    rows.extend(f"{red} {count}" for red, count in histogram)
    return "\n".join(rows) + "\n", f"{palette_error(histogram, k)}\n"
