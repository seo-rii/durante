"""An extra reuse requirement on large spans at the existing spatially material load sites."""
from fractions import Fraction

from first_run_sampler.profile import require, uint
from shape_calibration import shape

LARGE_SPAN_BYTES = 1 << 20


def _evaluate(reference_spans, current, spatial):
    current_spans = {s["offset"]: s["sampled_address_span_bytes"] for s in current["sites"]}
    details = []
    reasons = set()
    for site in spatial["material_sites"]:
        offset = site["offset"]
        a = reference_spans.get(offset)
        b = current_spans.get(offset)
        if a is None or b is None:
            reasons.add("missing_material_site")
        else:
            low, high = sorted((max(shape.SPAN_FLOOR, a), max(shape.SPAN_FLOOR, b)))
            if high >= LARGE_SPAN_BYTES and high * 2 > low * 3:
                reasons.add("large_span_changed")
        details.append({"offset": offset, "reference_span_bytes": a, "current_span_bytes": b})
    if not details:
        reasons.add("no_material_sites")
    return {"schema": 1, "status": "needs_native" if reasons else "reuse", "reasons": sorted(reasons),
            "reference_sample_sha256": spatial["reference_sample_sha256"],
            "current_sample_sha256": current["observation"]["sample_sha256"], "material_sites": details}


def compare(reference, current, spatial=None):
    """Evaluate the footprint restriction; the selector must also pass the spatial and temporal rules."""
    if spatial is None:
        spatial = shape.compare(reference, current)
    reference_spans = {s["offset"]: s["sampled_address_span_bytes"] for s in reference["sites"]}
    return _evaluate(reference_spans, current, spatial)


def validate(receipt, current, spatial):
    """Recheck the trusted selection receipt against the verified current full capture before execution."""
    reference_spans = {}
    for row in receipt["material_sites"]:
        offset = row["offset"]
        require(uint(offset) and offset not in reference_spans
                and all(row[k] is None or uint(row[k]) for k in ("reference_span_bytes", "current_span_bytes")),
                "invalid material-site footprint")
        reference_spans[offset] = row["reference_span_bytes"]
    require(type(receipt["schema"]) is int and receipt == _evaluate(reference_spans, current, spatial),
            "footprint comparison differs from this capture or reference")
    for site, spans in zip(spatial["material_sites"], receipt["material_sites"]):
        if site["span_ratio"] is not None:
            a, b = spans["reference_span_bytes"], spans["current_span_bytes"]
            require(a is not None and b is not None, "spatial comparison is missing a span")
            low, high = sorted((max(shape.SPAN_FLOOR, a), max(shape.SPAN_FLOOR, b)))
            require(site["span_ratio"] == str(Fraction(high, low)), "footprint spans differ from spatial comparison")
