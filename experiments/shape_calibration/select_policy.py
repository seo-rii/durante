"""Choose the earliest compatible trusted calibration, or obtain one native timing for this input."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from first_run_sampler.profile import digest, read_json, require, sha
from footprint_admission import admission
from guarded_native import anchor
from shape_calibration import combined, shape, temporal


METHODS = ("shape", "temporal", "footprint")


def permits_reuse(row, method):
    return (row["shape"]["status"] == "reuse"
            and (method == "shape" or row["temporal"]["status"] == "reuse")
            and (method != "footprint" or row["footprint"]["status"] == "reuse"))


def profiles(run, elf_sha, probe_sha, method):
    full = shape.inspect_run(run / "full", elf_sha)
    obs = full["observation"]
    require(obs["launch_count"] == 1, "selector requires a fresh full capture")
    extra = None
    if method != "shape":
        if (run / "combined.json").is_file():
            extra = combined.inspect_temporal(run, elf_sha, obs["stdin_sha256"], obs["stdout_sha256"], probe_sha)
        else:
            extra = temporal.inspect_run(run / "temporal", elf_sha, obs["stdin_sha256"], obs["stdout_sha256"], probe_sha)
    return full, extra


def bound_clock(run, elf, full):
    path = run / "policy.json"
    clock = anchor.load_clock(path, elf)
    raw = path.read_bytes()
    require(sha(raw) == clock.policy_sha256 and read_json(raw)["anchor"] == full["observation"],
            "native policy does not belong to this full capture")
    return clock


def current_clock(run, elf, full):
    # Both arms may request the same fallback concurrently. Serialize the native job and atomic publication.
    fd = os.open(run, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        if not (run / "policy.json").exists():
            anchor.calibrate(elf, run / "full/stdin", run / "full", run / "policy.json")
        return bound_clock(run, elf, full)
    finally:
        os.close(fd)


def resolve(elf, current, references, probe_sha, method):
    require(method in METHODS and digest(probe_sha), "invalid method or probe digest")
    elf = elf.resolve()
    current = current.resolve()
    references = [p.resolve() for p in references]
    require(len(set(references)) == len(references) and current not in references,
            "expected distinct earlier reference directories")
    elf_sha = anchor.static_elf(elf)
    full, extra = profiles(current, elf_sha, probe_sha, method)
    current_obs = full["observation"]
    comparisons = []
    chosen = None
    for index, run in enumerate(references):
        previous, previous_extra = profiles(run, elf_sha, probe_sha, method)
        require(previous["observation"]["result_sha256"] != current_obs["result_sha256"],
                "the reference is the current physical capture")
        clock = bound_clock(run, elf, previous)
        spatial = shape.compare(previous, full)
        order = temporal.compare(previous_extra, extra) if method != "shape" else None
        row = {"reference_index": index, "shape": spatial, "temporal": order}
        if method == "footprint":
            row["footprint"] = admission.compare(previous, full, spatial)
        comparisons.append(row)
        if permits_reuse(row, method):
            chosen = index
            break
    if chosen is None:
        clock = current_clock(current, elf, full)
    return {"schema": 1, "method": method, "action": "calibrate" if chosen is None else "reuse",
            "reference_index": chosen, "policy_sha256": clock.policy_sha256,
            "prediction_ns": clock.predict_ns(current_obs), "comparisons": comparisons,
            "current_sample_sha256": current_obs["sample_sha256"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--elf", required=True, type=Path)
    parser.add_argument("--run", required=True, type=Path, help="current capture directory")
    parser.add_argument("--reference", action="append", type=Path, default=[],
                        help="previously calibrated capture directory, oldest first (repeatable)")
    parser.add_argument("--method", required=True, choices=METHODS)
    parser.add_argument("--probe-sha256", required=True)
    parser.add_argument("--out", type=Path, help="new selection receipt file")
    args = parser.parse_args()
    decision = resolve(args.elf, args.run, args.reference, args.probe_sha256, args.method)
    if args.out:
        anchor.save_new(args.out, decision)
    print(json.dumps(decision))
