"""Find the best possible common affine correction across observed search cases."""

from fractions import Fraction
from statistics import median

from model import baseline, query_features


def possible(points, tolerance):
    """Intersect intervals for the slope after eliminating the common intercept."""
    lower_slope = upper_slope = None
    strips = []
    for x, base, visits, lo, hi in points:
        strips.append(
            (
                x,
                ((1 - tolerance) * lo - base) / visits,
                ((1 + tolerance) * hi - base) / visits,
            )
        )
    for x, lower, _ in strips:
        for y, _, upper in strips:
            # A common intercept needs lower - slope*x <= upper - slope*y.
            span = y - x
            if not span:
                if lower > upper:
                    return False
                continue
            edge = (upper - lower) / span
            if span > 0:
                if upper_slope is None or edge < upper_slope:
                    upper_slope = edge
            elif lower_slope is None or edge > lower_slope:
                lower_slope = edge
            if (
                lower_slope is not None
                and upper_slope is not None
                and lower_slope > upper_slope
            ):
                return False
    return True


def limits(cases, names, feature):
    central, favorable = [], []
    for name in names:
        native, samples = cases[name]
        for obs in samples:
            point = (
                query_features(obs["sample"])[feature],
                baseline(obs),
                obs["sample"]["visits"],
            )
            central.append((*point, median(native), median(native)))
            favorable.append((*point, min(native), max(native)))
    results = {}
    for name, points in (("median", central), ("favorable_endpoints", favorable)):
        low, high = -1, 1_000_000
        # At tolerance 100% a zero estimate is permitted by every target, but
        # common additive coefficients need not be able to cancel all baselines.
        while not possible(points, Fraction(high, 1_000_000)):
            high *= 2
        while high - low > 1:
            mid = (high + low) // 2
            if possible(points, Fraction(mid, 1_000_000)):
                high = mid
            else:
                low = mid
        results[name] = {
            "minimum_tolerance_ppm_ceiling": high,
            "possible_at_20_percent": high <= 200_000,
        }
    return results
