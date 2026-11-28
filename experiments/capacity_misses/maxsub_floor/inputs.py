"""Compact inputs and a segment-composition oracle for nonempty maximum subarray."""

def workload(scale):
    return {"short": 32_768, "long": 2_097_152}[scale], 71_362_147


def encode(count, seed):
    return f"{count} {seed}\n".encode()


def numbers(count, state):
    for _ in range(count):
        state ^= (state << 13) & 0xffffffff
        state ^= state >> 17
        state ^= (state << 5) & 0xffffffff
        yield state % 1001 - 500


def combine(left, right):
    ls, lp, lt, lb = left
    rs, rp, rt, rb = right
    return ls + rs, max(lp, ls + rp), max(rt, rs + lt), max(lb, rb, lt + rp)


def best(values):
    # Carry adjacent equal-sized segment summaries, then concatenate the
    # remaining binary-sized blocks in their original order.
    blocks = []
    for v in values:
        node = (v, v, v, v)
        level = 0
        while level < len(blocks) and blocks[level] is not None:
            node = combine(blocks[level], node)
            blocks[level] = None
            level += 1
        if level == len(blocks):
            blocks.append(node)
        else:
            blocks[level] = node
    answer = None
    for block in reversed(blocks):
        if block is not None:
            answer = block if answer is None else combine(answer, block)
    if answer is None:
        raise ValueError("expected at least one element")
    return answer[3]


def answer(count, seed):
    return f"{best(numbers(count, seed))}\n".encode()
