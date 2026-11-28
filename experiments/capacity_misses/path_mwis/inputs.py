"""A signed vertex-weight stream and a two-state independent-set oracle."""


def workload(scale):
    return {"short": 262_144, "long": 2_097_152}[scale], 29_840_173


def encode(count, seed):
    return f"{count} {seed}\n".encode()


def weights(count, state):
    for _ in range(count):
        state ^= (state << 13) & 0xffffffff
        state ^= state >> 17
        state ^= (state << 5) & 0xffffffff
        yield state % 1001 - 250


def optimum(values):
    excluded, included = 0, 0
    for weight in values:
        excluded, included = max(excluded, included), excluded + weight
    return max(excluded, included)


def answer(count, seed):
    return f"{optimum(weights(count, seed))}\n".encode()
