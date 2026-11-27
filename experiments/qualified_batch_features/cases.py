"""Reconstruct the registered inputs, oracle answers and binary identities without running any solver."""
import argparse
from array import array
from collections import deque
import hashlib
import json
from pathlib import Path
import subprocess
from types import ModuleType

AONOHAKO_COMMIT = "314b263c93bc898fb0b91f2e40d2482e2a1a7de2"
PILOT = "docs/durante/gce-qualified-judge"
# (array size, query count, min/max width, seed); (vertices, extra edges,
# max weight, seed); (batches, list size, key alphabet, seed).
INPUTS = {
    "range_min": ((131072, 1200000, 8, 512, 83174013),
                  (196608, 1600000, 8192, 32768, 1476347593)),
    "shortest_paths": ((40000, 380000, 13, 150841229),
                       (62000, 480000, 127, 2675108837)),
    "inversions": ((350, 4608, 1024, 237598237),
                   (384, 6144, 16384, 1265762041)),
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def pilot_sources(checkout):
    def read(name):
        return subprocess.check_output(["git", "-C", checkout, "show", f"{AONOHAKO_COMMIT}:{PILOT}/{name}"])

    source, registration = read("corpus.py"), read("registration.json")
    # Only the source file addressed by the pinned Git commit is executed.
    corpus = ModuleType("registered_aonohako_corpus")
    corpus.__file__ = str(checkout / PILOT / "corpus.py")
    exec(compile(source, corpus.__file__, "exec"), corpus.__dict__)
    return corpus, json.loads(registration), sha(source), sha(registration)


def short_interval_sum(corpus, params):
    """The incorrect solver omits the final item of each non-singleton interval."""
    n, queries, lo, hi, seed = params
    rng = corpus.numbers(seed)
    levels = [array("I", (1 + next(rng) % 1000003 for _ in range(n)))]
    step = 1
    while 2 * step <= hi:
        prev = levels[-1]
        levels.append(array("I", map(min, prev, prev[step:])))
        step *= 2
    total = 0
    for _ in range(queries):
        width = lo + next(rng) % (hi - lo + 1)
        left = next(rng) % (n - width + 1)
        width = max(1, width - 1)
        level = width.bit_length() - 1
        total += min(levels[level][left], levels[level][left + width - (1 << level)])
    return total


def hop_distance_sum(corpus, params):
    """The incorrect shortest-path solution reports unweighted hop distances."""
    n, extra, weight, seed = params
    rng = corpus.numbers(seed)
    graph = [[(i + 1) % n] for i in range(n)]
    for _ in range(n):
        next(rng)  # Consume the cycle edges' weights.
    for _ in range(extra):
        u = next(rng) % n
        v = (u + 1 + next(rng) % (n - 1)) % n
        next(rng)  # Consume this edge's weight.
        graph[u].append(v)
    dist = [-1] * n
    dist[0] = 0
    queue = deque([0])
    while queue:
        u = queue.popleft()
        for v in graph[u]:
            if dist[v] == -1:
                dist[v] = dist[u] + 1
                queue.append(v)
    return sum(dist)


def tie_count(corpus, params):
    """The incorrect inversion total includes each pair of equal keys."""
    batches, n, alphabet, seed = params
    rng = corpus.numbers(seed)
    total = 0
    for _ in range(batches):
        counts = [0] * alphabet
        for _ in range(n):
            key = next(rng) % alphabet
            total += counts[key]
            counts[key] += 1
    return total


def generate(checkout, binaries=None):
    corpus, registration, source_sha, registration_sha = pilot_sources(checkout)
    cases = []
    for problem in corpus.PROBLEMS:
        identity = registration["corpus"][problem]
        for variant, params in zip(("original", "a", "b"), (corpus.INPUTS[problem], *INPUTS[problem])):
            stdin = " ".join(map(str, params)) + "\n"
            answer = corpus.ORACLES[problem](*params)
            expected = f"{answer}\n"
            if variant == "original":
                if sha(stdin.encode()) != identity["stdin_sha256"] or sha(expected.encode()) != identity["stdout_sha256"]:
                    raise ValueError(f"calibration oracle differs from original registration for {problem}")
                outputs = {"reference": expected}
            else:
                if problem == "range_min":
                    wrong = short_interval_sum(corpus, params)
                elif problem == "shortest_paths":
                    wrong = hop_distance_sum(corpus, params)
                else:
                    wrong = answer + tie_count(corpus, params)
                if wrong == answer:
                    raise ValueError(f"incorrect solution passes {problem}/{variant}")
                outputs = {"fast": expected, "wrong": f"{wrong}\n"}
            for role, output in outputs.items():
                binary = f"{problem}-{role}"
                checksum = identity["elf_sha256"][role]
                if binaries and sha((binaries / binary).read_bytes()) != checksum:
                    raise ValueError(f"solver ELF differs from registration for {binary}")
                cases.append(dict(case_id=f"{problem}-{variant}-{role}",
                                  split="calibration" if variant == "original" else "held_out",
                                  problem=problem, variant=variant, role=role, binary=binary, binary_sha256=checksum,
                                  stdin=stdin, expected_stdout=expected, solver_stdout=output,
                                  stdin_sha256=sha(stdin.encode()), stdout_sha256=sha(expected.encode()),
                                  solver_stdout_sha256=sha(output.encode()),
                                  expected_status="Wrong Answer" if role == "wrong" else "Accepted"))
    return dict(schema=1, aonohako_commit=AONOHAKO_COMMIT, corpus_sha256=source_sha,
                registration_sha256=registration_sha, cases=cases)


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--aonohako-checkout", type=Path, required=True)
    cli.add_argument("--corpus-dir", type=Path, help="verify the prebuilt ELF files in the original pilot payload")
    cli.add_argument("--output", type=Path)
    opts = cli.parse_args()
    manifest = json.dumps(generate(opts.aonohako_checkout, opts.corpus_dir), indent=2) + "\n"
    if opts.output:
        opts.output.write_text(manifest, encoding="ascii")
    else:
        print(manifest, end="")
