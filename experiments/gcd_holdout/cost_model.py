"""Apply the control-fitted model to a canonical v9001 observation and loop sidecar."""

from fractions import Fraction
from functools import lru_cache
import hashlib
from pathlib import Path
import tarfile

from classify import site_mode

ROOT = Path(__file__).resolve().parents[2]


@lru_cache(maxsize=16)
def executable(archive, member, expected):
    with tarfile.open(ROOT / archive) as container:
        entry = container.getmember(member)
        if not entry.isfile() or entry.size > 16 * 1024 * 1024:
            raise ValueError("unexpected archived executable")
        raw = container.extractfile(entry).read()
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError("executable hash mismatch")
    return raw


@lru_cache(maxsize=1024)
def mode_for(archive, member, expected, target, branch):
    return site_mode(executable(archive, member, expected), target, branch)


def baseline(observation, fit):
    prod = observation["production"]
    if prod["latency_bound"]["version"] != 9001:
        raise ValueError("the fitted model requires the experimental v9001 counter")
    weights = fit["baseline_coefficients_ns"]
    return (
        Fraction(weights["latency_cycles"]) * prod["latency_bound"]["cycles_x16"] / 16
        + Fraction(weights["memory_bytes"]) * prod["memory_operand_bytes"]["bytes"]
    )


def estimate(observation, case, fit):
    visits = changes = 0
    by_site = []
    for site in observation["trip"]["sites"]:
        mode = mode_for(
            case["binary_archive"],
            case["archive_member"],
            case["binary_sha256"],
            site["target_offset"],
            site["branch_offset"],
        )
        if mode not in {"remainder", "counted", "other"}:
            raise ValueError("unknown loop classification")
        if mode in {"remainder", "counted"}:
            if site["pending_length"]:
                raise ValueError("incomplete Euclidean loop in timing observation")
            visits += site["visits"]
            if mode == "remainder":
                changes += site["changes"]
        by_site.append({"branch_offset": site["branch_offset"], "mode": mode})
    fast = baseline(observation, fit)
    correction = visits * Fraction(fit["ns_per_eligible_visit"]) + changes * Fraction(
        fit["ns_per_remainder_length_change"]
    )
    return (
        fast,
        fast + correction,
        {
            "sites": by_site,
            "eligible_visits": visits,
            "remainder_length_changes": changes,
        },
    )
