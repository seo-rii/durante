"""Reconstruct the recorded children and folders in the order fixed by each study."""
import hashlib
import json
from pathlib import Path


def sample_layout(dataset):
    root = Path(__file__).resolve().parent
    holdout = "fixtures_sha256" in dataset
    protocol_file = (root / "holdout" if holdout else root) / "protocol.json"
    raw = protocol_file.read_bytes()
    if dataset["complete"] is not True or hashlib.sha256(raw).hexdigest() != dataset["protocol_sha256"]:
        raise ValueError("incomplete dataset or unrecognized protocol")
    plan = json.loads(raw)
    cases = [(name, scale) for name in plan["programs"] for scale in plan["scales"]]
    sequence = [(name, scale, rep) for rep, offset in enumerate(plan["round_rotations"], 1)
                for name, scale in cases[offset:] + cases[:offset]]
    for i, (row, expected) in enumerate(zip(dataset["samples"], sequence, strict=True)):
        name, scale, rep = expected
        if (row["workload_id"], row["scale"], row["repetition"]) != expected:
            raise ValueError("case order differs from the preregistered sequence")
        modes = ("metered",)
        if holdout:
            modes = ("full", "fast") if i in plan["full_first_indices"] else ("fast", "full")
        phases = ("native_before", *modes, "native_after")
        if holdout and tuple(row["phase_order"]) != phases:
            raise ValueError("diagnostic mode order differs from the preregistered sequence")
        yield row, f"samples/{i+1:02d}-{name}-{scale}-{rep}", phases
