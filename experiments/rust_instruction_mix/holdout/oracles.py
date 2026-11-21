"""Generate the reference stdin and byte-exact output without running a benchmark ELF."""
import hashlib
import itertools
import json
from math import gcd
from pathlib import Path


HERE = Path(__file__).resolve().parent


def random_words(state):
    while True:
        state ^= (state << 13) & 0xffffffff
        state ^= state >> 17
        state ^= (state << 5) & 0xffffffff
        yield state


def expected_gcd(count, seed):
    words = random_words(seed)
    return sum(gcd(max(1, next(words) & 0x7fffffff), max(1, next(words) & 0x7fffffff))
               for _ in range(count))


def expected_lower_bound(size, count, seed):
    # The search space contains exactly the even keys 0, 2, ..., 2*(size-1).
    return sum(min(size, (x % (2*size + 1) + 1) // 2)
               for x in itertools.islice(random_words(seed), count))


def expected_rolling_window(size, sweeps, seed):
    values = [x & 0xffff for x in itertools.islice(random_words(seed), size)]
    total = sum(values)
    answer = 0
    for sweep in range(sweeps):
        width = 257 + 2*sweep
        # Each interior element belongs to width windows. Reflect the missing
        # windows at the two edges to count occurrences without sliding a window.
        assert 2*width <= size
        edges = sum((width-i-1) * (values[i] + values[-1-i]) for i in range(width-1))
        answer += width*total - edges
    return answer


def manifest():
    programs = {}
    for name, parameters, oracle in (
        ('gcd', (524288, 1486885521), expected_gcd),
        ('lower_bound', (262144, 524288, 15485863), expected_lower_bound),
        ('rolling_window', (1048576, 16, 32452843), expected_rolling_window),
    ):
        stdin = ' '.join(map(str, parameters)) + '\n'
        stdout = str(oracle(*parameters)) + '\n'
        programs[name] = {'source_sha256': hashlib.sha256((HERE/f'{name}.rs').read_bytes()).hexdigest(),
                          'cases': [{'scale': 1, 'stdin': stdin, 'stdout': stdout,
                                     'input_sha256': hashlib.sha256(stdin.encode()).hexdigest(),
                                     'expected_sha256': hashlib.sha256(stdout.encode()).hexdigest()}]}
    return {'schema': 1, 'compiler_version': 'rustc 1.95.0 (59807616e 2026-04-14)',
            'compiler_flags': ['--edition', '2024', '-O', '--cfg', 'ONLINE_JUDGE'], 'programs': programs}


if __name__ == '__main__':
    print(json.dumps(manifest(), indent=2))
