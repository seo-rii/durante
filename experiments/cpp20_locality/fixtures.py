"""Reproduce the exact inputs, outputs and sparse addresses of the frozen pointer walk."""
import hashlib
import json
from pathlib import Path
import sys

BINARY_SHA256 = "ecf2c6d2ae86980640e884929410f95a0df1a2f2ae4625cd75b3698f896f95b2"
STEPS = 2_097_573
PERIOD = 1024
CASES = (("32KiB", 32 << 10), ("128KiB", 128 << 10), ("2MiB", 2 << 20),
         ("16MiB", 16 << 20), ("64MiB", 64 << 20))


def affine(steps, mask):
    """Return multiplier and addend for the composition of steps LCG transitions."""
    a, b = 1, 0
    c, d = 1_664_525, 1_013_904_223
    while steps:
        if steps & 1:
            a, b = a * c & mask, (b * c + d) & mask
        c, d = c * c & mask, ((1 + c) * d) & mask
        steps >>= 1
    return a, b


def manifest():
    cases = []
    for label, active_bytes in CASES:
        entries = active_bytes // 4
        mask = entries - 1
        seed = 713 & mask
        jump_a, jump_b = affine(PERIOD, mask)
        finish_a, finish_b = affine(STEPS, mask)
        index = seed
        near = [0] * 16
        pairs = 0
        lo, hi = entries, -1
        for t in range(0, STEPS, PERIOD):
            lo, hi = min(lo, index), max(hi, index)
            if t + 1 < STEPS:
                nxt = (1_664_525 * index + 1_013_904_223) & mask
                lo, hi = min(lo, nxt), max(hi, nxt)
                pairs += 1
                for phase in range(16):
                    near[phase] += abs((index + phase) // 16 - (nxt + phase) // 16) <= 1
            index = (jump_a * index + jump_b) & mask
        stdin = f"{entries} {STEPS}\n"
        stdout = f"{(finish_a * seed + finish_b) & mask}\n"
        cases.append({
            "case_id": "cycle_walk/" + label, "program": "cycle_walk",
            "split": "repeat" if label in ("32KiB", "64MiB") else "fresh",
            "active_bytes": active_bytes, "steps": STEPS, "binary_sha256": BINARY_SHA256,
            "stdin": stdin, "stdout": stdout, "stdin_sha256": hashlib.sha256(stdin.encode()).hexdigest(),
            "stdout_sha256": hashlib.sha256(stdout.encode()).hexdigest(),
            "oracle": {"offset": 0x1A00, "visits": STEPS, "pairs": pairs,
                       "sampled_min_index": lo, "sampled_max_index": hi,
                       "sampled_span_bytes": 4 * (hi - lo),
                       "near_pairs_by_base_mod64_div4": near},
        })
    # These two native-output oracles and ELF digests come from the frozen
    # Aonohako CPP20 corpus at b938afaa6c171a8a56e24d376d6662f93fcea4c6.
    for program, stdin, stdout, digest in (
        ("sort", "524288\n", "48038533464326144\n",
         "d96565357cdd163ea7de5eb145aaf915b0b0ba9e238a46718e9e1352e51c4b75"),
        ("rep_copy", "4194304 512\n", "1121503238200285\n",
         "255445d9e1311d5872c4da62e12df42c199012cfbc9b8c5dc6e8a8646cab2100"),
    ):
        cases.append({"case_id": program + "/1", "program": program, "split": "cost_only",
                      "stdin": stdin, "stdout": stdout, "binary_sha256": digest,
                      "stdin_sha256": hashlib.sha256(stdin.encode()).hexdigest(),
                      "stdout_sha256": hashlib.sha256(stdout.encode()).hexdigest()})
    return cases


if __name__ == "__main__":
    content = json.dumps(manifest(), indent=2) + "\n"
    if len(sys.argv) == 2:
        if Path(sys.argv[1]).read_text() != content:
            raise SystemExit("fixture manifest differs from recomputed oracle")
    else:
        print(content, end="")
