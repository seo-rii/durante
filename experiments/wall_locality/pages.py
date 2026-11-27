"""Reference count for the logical endpoint pages of a text-grader Wall input."""
from collections import OrderedDict
from io import StringIO


def reference_counts(text):
    words = (word for line in StringIO(text) for word in line.split())
    n, k = int(next(words)), int(next(words))
    recent = OrderedDict()
    misses = 0
    for _ in range(k):
        operation, left, right, height = (int(next(words)) for _ in range(4))
        if operation not in (1, 2) or not 0 <= left <= right < n or not 0 <= height <= 100000:
            raise ValueError("invalid Wall update")
        for cell in (left, right):
            page = cell // 512
            if page in recent:
                recent.move_to_end(page)
            else:
                misses += 1
                if len(recent) == 16:
                    recent.popitem(last=False)
                recent[page] = None
    if next(words, None) is not None:
        raise ValueError("extra Wall input")
    return {"accesses": 2 * k, "misses": misses}
