"""Equal-size texts with sparse planted occurrences and different mismatch positions."""

LENGTH = 1 << 20
PATTERN = b'a' * 47 + b'b'
SEED = 20261128


def workload(case):
    if case == "periodic":
        text = bytearray(b'a' * LENGTH)
    elif case == "random":
        state = SEED
        text = bytearray(LENGTH)
        for i in range(LENGTH):
            state ^= (state << 13) & 0xffffffff
            state ^= state >> 17
            state ^= (state << 5) & 0xffffffff
            text[i] = ord('a') + (state & 15)
    else:
        raise ValueError("unknown text")
    for pos in range(1024, LENGTH, 8192):
        text[pos:pos + len(PATTERN)] = PATTERN
    return PATTERN, bytes(text)


def encode(pattern, text):
    return pattern + b'\n' + text + b'\n'


def answer(pattern, text):
    if not pattern:
        raise ValueError("pattern must be nonempty")
    start = text.find(pattern)
    count = 0
    while start >= 0:
        count += 1
        start = text.find(pattern, start + 1)
    return f'{count}\n'.encode()
