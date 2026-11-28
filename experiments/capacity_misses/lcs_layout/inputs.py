"""Transpose a fixed pair of byte strings and independently score their LCS."""

def workload(shape):
    state = 17764831
    def letters(length):
        nonlocal state
        result = bytearray()
        for _ in range(length):
            state ^= (state << 13) & 0xffffffff
            state ^= state >> 17
            state ^= (state << 5) & 0xffffffff
            result.append(97 + state % 8)
        return bytes(result)
    short, long = letters(1152), letters(2304)
    if shape == "wide":
        return short, long
    if shape == "tall":
        return long, short
    raise ValueError("unknown matrix shape")


def encode(a, b):
    return a + b'\n' + b + b'\n'


def answer(a, b):
    masks = {}
    for j, c in enumerate(b):
        masks[c] = masks.get(c, 0) | (1 << j)
    # Bit j marks where the LCS length rises in the current prefix row.
    row = 0
    for c in a:
        x = row | masks.get(c, 0)
        row = x & ~(x - ((row << 1) | 1))
    return f"{row.bit_count()}\n".encode()
