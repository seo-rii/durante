"""Find the time range any common nonnegative work coefficients can predict across two submissions."""
from fractions import Fraction
from math import ceil

from contest_submissions.score import samples
from cpp20_locality.verify import require
from guarded_native.score import MS


def bounds(reference_counter, submission_counter, reference_samples_ns, native_samples_ns):
    """Compare each model family's full attainable interval with the observed submission interval."""
    samples(reference_samples_ns, 3)
    samples(native_samples_ns, 9)
    target_lo, target_hi = min(native_samples_ns), max(native_samples_ns)
    result = {}
    for model, columns in (("instruction_rep", ("instructions", "rep_bytes")),
                           ("latency_memory", ("cycles_x16", "memory_operand_bytes"))):
        ratios = []
        unbounded = False
        for column in columns:
            a, b = reference_counter.get(column), submission_counter.get(column)
            require(all(type(v) is int and 0 <= v < 2**64 for v in (a, b)), f"invalid {column} counter")
            if a:
                ratios.append(Fraction(b, a))
            elif b:
                unbounded = True
        require(ratios, f"{model} has no positive reference work")
        # With positive reference work, the combined ratio is a weighted mean of b/a.
        # A submission-only feature can increase its numerator without changing its denominator.
        lower = ceil(min(reference_samples_ns) * min(ratios))
        upper = None if unbounded else ceil(max(reference_samples_ns) * max(ratios))
        error, direction = 0, None
        if upper is not None and upper < target_lo:
            error, direction = MS * (target_lo - upper) // target_lo, "under"
        elif lower > target_hi:
            error, direction = MS * (lower - target_hi) // target_hi, "over"
        result[model] = dict(lower_ns=lower, upper_ns=upper, minimum_error_ppm=error, direction=direction)
    return result
