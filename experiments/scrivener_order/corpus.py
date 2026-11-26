"""Hold Scrivener's edit history and lookup positions fixed while changing their order."""
from array import array
from io import StringIO
from random import Random

from contest_scale.variants import editor, letter

NAMES = ("ascending", "tiled", "shuffled", "interwoven_pages")
SIZE = 180_000
TILE_SIZE = 256


def matched_case(name, size=SIZE):
    if name not in NAMES[:3]:
        raise KeyError(name)
    if size < 1:
        raise ValueError("the text must contain at least one letter")
    rng = Random(20121208)
    positions = [rng.randrange(size) for _ in range(size)]
    positions[0], positions[-1] = 0, size - 1
    positions.sort()

    order = Random(20121209)
    if name == "tiled":
        for i in range(0, size, TILE_SIZE):
            tile = positions[i:i + TILE_SIZE]
            order.shuffle(tile)
            positions[i:i + TILE_SIZE] = tile
    elif name == "shuffled":
        order.shuffle(positions)

    prefix = "".join(letter(0, i) for i in range(size))
    text = StringIO()
    text.write(f"{2 * size}\n")
    text.writelines(f"T {ch}\n" for ch in prefix)
    text.writelines(f"P {p}\n" for p in positions)
    return text.getvalue(), "".join(prefix[p] + "\n" for p in positions)


def make(name):
    if name == "interwoven_pages":
        return editor(name)
    return matched_case(name)


def count_queries(text):
    """Count followed query edges in the two implementations on an append-only history."""
    commands = StringIO(text)
    operation_count = int(next(commands))
    # An append-only history has one node per depth. Node d's parent has depth d - 1.
    jumps = array("I", [0])
    seen_positions = set()
    prints = compact = lifting = 0
    for line in commands:
        command, value = line.split()
        depth = len(jumps) - 1
        if command == "T":
            first = jumps[depth]
            second = jumps[first]
            jumps.append(second if depth - first == first - second else depth)
        elif command == "P":
            position = int(value)
            if not 0 <= position < depth:
                raise ValueError("print outside the current text")
            prints += 1
            seen_positions.add(position)
            target = position + 1
            # A lifting-table query follows one edge for each set bit in the distance.
            lifting += (depth - target).bit_count()
            while depth > target:
                jump = jumps[depth]
                depth = jump if jump >= target else depth - 1
                compact += 1
        else:
            raise ValueError("expected an append or print command")
    typed = len(jumps) - 1
    if typed + prints != operation_count:
        raise ValueError("Scrivener command count differs from the header")
    return dict(type_commands=typed, print_commands=prints, unique_positions=len(seen_positions),
                compact_query_steps=compact, lifting_query_steps=lifting)


def query_stats(name):
    return count_queries(make(name)[0])
