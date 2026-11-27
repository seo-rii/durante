"""Matched Scrivener query streams and their logical page locality."""
from collections import OrderedDict
from itertools import zip_longest
from random import Random

from scrivener_order.corpus import SIZE, matched_case

NAMES = ("ascending", "shuffled", "bit_reverse", "alternate_extremes",
         "lanes16", "lanes32", "shuffled_blocks")


def _ranks(name, size):
    if name == "bit_reverse":
        bits = (size - 1).bit_length()
        for i in range(1 << bits):
            rank = int(format(i, f"0{bits}b")[::-1], 2)
            if rank < size:
                yield rank
    elif name == "alternate_extremes":
        for i in range(size):
            yield i // 2 if i % 2 == 0 else size - 1 - i // 2
    elif name in ("lanes16", "lanes32"):
        lanes = int(name[5:])
        runs = [range(i * size // lanes, (i + 1) * size // lanes) for i in range(lanes)]
        for column in zip_longest(*runs):
            yield from (rank for rank in column if rank is not None)
    elif name == "shuffled_blocks":
        starts = list(range(0, size, 512))
        Random(20121127).shuffle(starts)
        for start in starts:
            yield from range(start, min(start + 512, size))
    else:
        raise ValueError(f"unknown Scrivener order {name}")


def make(name, size=SIZE):
    if name in ("ascending", "shuffled"):
        return matched_case(name, size)
    text, _ = matched_case("ascending", size)
    commands = text.splitlines(keepends=True)
    letters = "".join(cmd.split()[1] for cmd in commands[1:size + 1])
    positions = [int(cmd.split()[1]) for cmd in commands[size + 1:]]
    ordered = [positions[rank] for rank in _ranks(name, size)]
    return ("".join(commands[:size + 1]) + "".join(f"P {position}\n" for position in ordered),
            "".join(letters[position] + "\n" for position in ordered))


def _number(token):
    if token is None or not token.isdigit():
        raise ValueError("expected an unsigned decimal integer")
    digits = token.lstrip(b"0") or b"0"
    if len(digits) > 10 or int(digits) > 2**31 - 1:
        raise ValueError("integer out of range")
    return int(digits)


def features(text):
    """Count accesses to cold 512-position pages and distances between print queries."""
    words = iter(text.encode("ascii").split())
    operations = _number(next(words, None))
    recent = OrderedDict()
    length = accesses = misses = bits = 0
    previous = None
    for _ in range(operations):
        command, value = next(words, None), next(words, None)
        if command == b"T" and value is not None and len(value) == 1 and b"a" <= value <= b"z":
            length += 1
        elif command == b"P":
            position = _number(value)
            if position >= length:
                raise ValueError("position outside current text")
            accesses += 1
            if previous is not None:
                bits += abs(position - previous).bit_length()
            previous = position
            page = position // 512
            if page in recent:
                recent.move_to_end(page)
            else:
                misses += 1
                if len(recent) == 16:
                    recent.popitem(last=False)
                recent[page] = None
        else:
            raise ValueError("expected a lowercase append or print query")
    if next(words, None) is not None:
        raise ValueError("extra Scrivener input")
    return {"accesses": accesses, "misses": misses, "position_bits": bits}
