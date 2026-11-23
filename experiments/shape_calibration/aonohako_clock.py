"""Export diagnostic Aonohako work-clock weights for one trusted preflight and explicit deadlines."""
import argparse
from fractions import Fraction
from heapq import nsmallest
import json
from math import lcm
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from first_run_sampler.profile import U64_MAX, elf_digest, read_json, require, sha
from guarded_native import anchor
from shape_calibration import run, select_policy, temporal

NS_PER_MS = 1_000_000
MAX_LIMITS = 16
SEARCH_DENOMINATORS = 131_072
CANDIDATES_PER_SIDE = 32


def budgets(limits_ms):
    require(isinstance(limits_ms, (list, tuple)) and 0 < len(limits_ms) <= MAX_LIMITS
            and all(type(ms) is int and 0 < ms <= (2**63 - 1) // NS_PER_MS for ms in limits_ms)
            and len(set(limits_ms)) == len(limits_ms), "expected 1 to 16 distinct positive signed-duration millisecond limits")
    return [ms * NS_PER_MS for ms in sorted(limits_ms)]


def count_within(instruction, rep, budget):
    """Count nonnegative integer pairs with instruction*i + rep*r <= budget, boundary included."""
    max_i, remainder = divmod(budget, instruction)
    n = max_i + 1
    # Reverse i=0..max_i to sum 1+floor((instruction*i+remainder)/rep).
    count = n
    while True:
        quotient, instruction = divmod(instruction, rep)
        count += quotient * n * (n - 1) // 2
        quotient, remainder = divmod(remainder, rep)
        count += quotient * n
        # Transpose the remaining staircase. Its height is smaller at each Euclidean step.
        height = instruction * n + remainder
        if height < rep:
            return count
        n, remainder = divmod(height, rep)
        rep, instruction = instruction, rep


def rounded_candidates(instruction, rep, denominator, largest, upper):
    # Rank same-direction approximations near the largest usable denominator. Score is
    # proportional to the sum of relative coefficient errors at a fixed denominator.
    for d in range(largest, max(0, largest - SEARCH_DENOMINATORS), -1):
        a, a_rem = divmod(instruction * d, denominator)
        b, b_rem = divmod(rep * d, denominator)
        if upper:
            if a_rem:
                a += 1
                a_rem = denominator - a_rem
            if b_rem:
                b += 1
                b_rem = denominator - b_rem
        if a and b:
            yield (a_rem * rep + b_rem * instruction, -d, a, b)



def project(instruction_ns, rep_ns, limits_ms):
    deadlines = budgets(limits_ms)
    require(all(isinstance(value, Fraction) and value > 0 and
                max(value.numerator.bit_length(), value.denominator.bit_length()) <= 1024
                for value in (instruction_ns, rep_ns)), "unsupported calibrated coefficient size or sign")
    denominator = lcm(instruction_ns.denominator, rep_ns.denominator)
    instruction, rep = int(instruction_ns * denominator), int(rep_ns * denominator)
    if max(instruction, rep, denominator) <= U64_MAX:
        return instruction, rep, denominator
    largest = min(U64_MAX, U64_MAX * denominator // max(instruction, rep))
    require(largest > 0, "calibrated coefficient exceeds the unsigned 64-bit range")
    exact_counts = [count_within(instruction, rep, t * denominator) for t in deadlines]
    for upper in (False, True):
        candidates = rounded_candidates(instruction, rep, denominator, largest, upper)
        for _, neg_d, a, b in nsmallest(CANDIDATES_PER_SIDE, candidates):
            d = -neg_d
            # Both coefficients round in the same direction, so the two finite sets are
            # nested. Equal counts prove equal decisions for every pair, including ties.
            if all(count_within(a, b, t * d) == expected for t, expected in zip(deadlines, exact_counts)):
                return a, b, d
    raise ValueError("bounded search found no certified uint64 clock for these deadlines")


def export(selection_file, current, policy_file, elf, stdin, limits_ms):
    deadlines = budgets(limits_ms)
    selected_raw = selection_file.read_bytes()
    selected = read_json(selected_raw)
    policy_raw = policy_file.read_bytes()
    clock = anchor.load_clock(policy_file, elf)
    require(sha(policy_raw) == clock.policy_sha256, "calibration policy changed")
    policy = read_json(policy_raw)
    require(policy["native"]["single_cpu"] is True and clock.native_ns <= U64_MAX,
            "export requires a single-CPU calibration within uint64 nanoseconds")
    require(selected["method"] in select_policy.METHODS, "unknown preflight method")
    full, extra = select_policy.profiles(current, clock.elf_sha256, temporal.PROBE_SHA, selected["method"])
    run.check_selection(selected, full, extra, policy, clock)
    obs = full["observation"]
    require(elf_digest(stdin) == obs["stdin_sha256"], "requested stdin differs from the selected capture")
    scale = Fraction(clock.native_ns) / clock.calibration_work
    a, b, d = project(scale * clock.weights_ns["instructions"], scale * clock.weights_ns["rep_bytes"], limits_ms)
    return {"schema": 1, "kind": "aonohako_input_deadline_projection", "authority": "diagnostic_only",
            "elf_sha256": clock.elf_sha256, "stdin_sha256": obs["stdin_sha256"],
            "current_result_sha256": obs["result_sha256"], "current_sample_sha256": obs["sample_sha256"],
            "selection_sha256": sha(selected_raw), "selection_method": selected["method"], "action": selected["action"],
            "policy_sha256": clock.policy_sha256, "calibration_sha256": policy["calibration_sha256"],
            "reference_sha256": policy["reference_sha256"], "limits_ms": [t // NS_PER_MS for t in deadlines],
            "weights": {"method": "weighted-work-nanoseconds-v1", "instruction_ns_numerator": a,
                        "rep_byte_ns_numerator": b, "ns_denominator": d}}


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--selection", required=True, type=Path, help="trusted select_policy.py receipt")
    cli.add_argument("--run", required=True, type=Path, help="original capture for the current input")
    cli.add_argument("--policy", required=True, type=Path, help="selected paid calibration")
    cli.add_argument("--elf", required=True, type=Path, help="immutable trusted static ELF")
    cli.add_argument("--stdin", required=True, type=Path)
    cli.add_argument("--limit-ms", required=True, action="append", type=int)
    cli.add_argument("--out", required=True, type=Path, help="new private projection receipt")
    args = cli.parse_args()
    try:
        receipt = export(args.selection, args.run, args.policy, args.elf, args.stdin, args.limit_ms)
        anchor.save_new(args.out, receipt)
    except (OSError, ValueError, KeyError, TypeError, IndexError, OverflowError) as error:
        cli.exit(1, f"projection unavailable: {error}\n")
