"""Compare two full, PID-linked sparse address reports from the same trusted executable."""
import argparse
from fractions import Fraction
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from first_run_sampler.profile import MAX_RECEIPT_BYTES, calibration_sites, read_json, require, sha
from guarded_native.anchor import measured, static_elf

MAX_TV = Fraction(1, 8)
MATERIAL_SHARE = Fraction(1, 20)
MIN_COVERAGE = Fraction(4, 5)
MIN_PAIRS = 64
MAX_FAR_CHANGE = Fraction(1, 16)
SPAN_FLOOR = 4096
MAX_SPAN_RATIO = 4


def inspect_run(run, elf_sha):
    """Verify the actual selected launch and bind its full sample to the terminal counters."""
    obs = measured(run, elf_sha)
    require(obs["mode"] == "full", "shape comparison requires a full terminal sample")
    with (run / "full/sample.json").open("rb") as stream:
        raw = stream.read(MAX_RECEIPT_BYTES + 1)
    require(0 < len(raw) <= MAX_RECEIPT_BYTES and sha(raw) == obs["sample_sha256"],
            "full sample changed or exceeds the size limit")
    # measured() also validates a legitimate empty report against its PID and zero totals.
    sites = calibration_sites(raw, obs["pid"]) if read_json(raw)["sites"] else []
    return {"observation": obs, "sites": sites}


def compare(reference, current):
    """Apply the fixed rules to two profiles returned by inspect_run()."""
    left, right = reference["observation"], current["observation"]
    require(left["elf_sha256"] == right["elf_sha256"] and left["mode"] == right["mode"] == "full",
            "incompatible executable or sampler")
    base = {s["offset"]: s for s in reference["sites"]}
    new = {s["offset"]: s for s in current["sites"]}
    a_total = sum(s["loads"] for s in base.values())
    b_total = sum(s["loads"] for s in new.values())
    result = {"schema": 1, "status": "needs_native", "reasons": [],
              "reference_sample_sha256": left["sample_sha256"], "current_sample_sha256": right["sample_sha256"],
              "total_variation": None, "material_coverage": [], "material_sites": []}
    if not a_total or not b_total:
        result["reasons"].append("empty_full_sample")
        return result

    reasons = set()
    tv_numerator = a_covered = b_covered = 0
    for offset in sorted(base.keys() | new.keys()):
        a, b = base.get(offset, {}), new.get(offset, {})
        a_loads, b_loads = a.get("loads", 0), b.get("loads", 0)
        tv_numerator += abs(a_loads * b_total - b_loads * a_total)
        a_share, b_share = Fraction(a_loads, a_total), Fraction(b_loads, b_total)
        if max(a_share, b_share) < MATERIAL_SHARE:
            continue
        a_covered += a_loads
        b_covered += b_loads
        detail = {"offset": offset, "reference_share": str(a_share),
                  "current_share": str(b_share), "far_change": None,
                  "span_ratio": None, "reasons": []}
        if min(a.get("pairs", 0), b.get("pairs", 0)) < MIN_PAIRS:
            detail["reasons"].append("insufficient_pairs")
        else:
            change = abs(Fraction(a["far_pairs"], a["pairs"]) - Fraction(b["far_pairs"], b["pairs"]))
            spans = [max(SPAN_FLOOR, s["sampled_address_span_bytes"]) for s in (a, b)]
            ratio = Fraction(max(spans), min(spans))
            detail.update(far_change=str(change), span_ratio=str(ratio))
            if change > MAX_FAR_CHANGE:
                detail["reasons"].append("far_fraction_changed")
            if ratio > MAX_SPAN_RATIO:
                detail["reasons"].append("span_changed")
        reasons.update(detail["reasons"])
        result["material_sites"].append(detail)

    tv = Fraction(tv_numerator, 2 * a_total * b_total)
    coverage = (Fraction(a_covered, a_total), Fraction(b_covered, b_total))
    if tv > MAX_TV:
        reasons.add("site_shares_changed")
    if min(coverage) < MIN_COVERAGE:
        reasons.add("insufficient_material_coverage")
    result.update(status="needs_native" if reasons else "reuse", reasons=sorted(reasons),
                  total_variation=str(tv), material_coverage=[str(f) for f in coverage])
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--elf", required=True, type=Path)
    parser.add_argument("--reference", required=True, type=Path, help="trusted #82 run with full final report")
    parser.add_argument("--current", required=True, type=Path, help="another full run for this executable")
    args = parser.parse_args()
    binary = static_elf(args.elf)
    print(json.dumps(compare(inspect_run(args.reference, binary), inspect_run(args.current, binary))))
