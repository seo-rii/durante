"""Enforce the selected clock on the exact preflighted input of a trusted static executable."""
import argparse
import json
from pathlib import Path
import shutil
import sys
import tempfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from first_run_sampler.profile import elf_digest, read_json, require, sha
from guarded_native import anchor, live
from shape_calibration import select_policy, temporal


def check_selection(selection, current, extra, policy, clock):
    obs = current["observation"]
    method = selection["method"]
    require(type(selection["schema"]) is int and selection["schema"] == 1 and method in ("shape", "temporal")
            and type(selection["prediction_ns"]) is int and selection["prediction_ns"] == clock.predict_ns(obs)
            and selection["current_sample_sha256"] == obs["sample_sha256"]
            and selection["policy_sha256"] == clock.policy_sha256, "selection differs from this policy or capture")
    comparisons = selection["comparisons"]
    require(isinstance(comparisons, list), "invalid selection comparisons")
    eligible = []
    for i, row in enumerate(comparisons):
        shape, order = row["shape"], row["temporal"]
        require(type(row["reference_index"]) is int and row["reference_index"] == i
                and shape["current_sample_sha256"] == obs["sample_sha256"]
                and shape["status"] in ("reuse", "needs_native"), "invalid spatial comparison")
        if method == "temporal":
            require(order["current_sample_sha256"] == extra["observation"]["sample_sha256"]
                    and order["status"] in ("reuse", "needs_native"), "invalid temporal comparison")
        else:
            require(order is None, "unexpected temporal comparison")
        if shape["status"] == "reuse" and (order is None or order["status"] == "reuse"):
            eligible.append(i)
    index = selection["reference_index"]
    if selection["action"] == "calibrate":
        require(index is None and not eligible and policy["anchor"] == obs,
                "calibration policy does not belong to this input")
    else:
        require(selection["action"] == "reuse" and type(index) is int and eligible == [index]
                and index == len(comparisons) - 1 and comparisons[index]["shape"]["reference_sample_sha256"]
                == policy["anchor"]["sample_sha256"], "reused policy does not match the selected reference")


def run(payload, selection_file, current, policy_file, elf, stdin, out, limit_ms, timeout_ms=120000, poll_ms=5):
    out.mkdir(mode=0o700)
    result = {"schema": 1, "authority": "diagnostic_only", "status": "unavailable", "pid": None}
    try:
        selected_raw = selection_file.read_bytes()
        selection = read_json(selected_raw)
        require(selection["method"] in ("shape", "temporal"), "unknown selection method")
        with tempfile.TemporaryDirectory(dir=out) as tmp:
            # Keep the checked policy and stdin stable across verification and the live launch.
            private = Path(tmp)
            saved_policy, saved_input = private / "policy.json", private / "stdin"
            policy_raw = policy_file.read_bytes()
            saved_policy.write_bytes(policy_raw)
            clock = anchor.load_clock(saved_policy, elf)
            full, extra = select_policy.profiles(current, clock.elf_sha256, temporal.PROBE_SHA, selection["method"])
            obs = full["observation"]
            check_selection(selection, full, extra, read_json(policy_raw), clock)
            shutil.copyfile(current / "full/stdin", saved_input)
            require(obs["stdin_sha256"] == elf_digest(saved_input) == elf_digest(stdin),
                    "requested stdin differs from the full capture")
            result = live.run(payload, saved_policy, elf, saved_input, out / "live", limit_ms, timeout_ms, poll_ms)
            result = {**result, "selection_sha256": sha(selected_raw), "method": selection["method"],
                      "action": selection["action"], "reference_index": selection["reference_index"],
                      "current_sample_sha256": obs["sample_sha256"], "current_result_sha256": obs["result_sha256"],
                      "preflight_prediction_ns": selection["prediction_ns"]}
    except (OSError, ValueError, KeyError, IndexError, TypeError, OverflowError) as error:
        result["reason"] = str(error)
    anchor.save_new(out / "result.json", result)
    return result


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    for flag, description in (("payload", "registered #82 runtime"), ("selection", "trusted select_policy.py receipt"),
                              ("run", "the original current full/temporal capture"), ("policy", "selected policy.json"),
                              ("elf", "immutable trusted static ELF"), ("stdin", "input to compare with the capture"),
                              ("out", "new output directory")):
        cli.add_argument("--" + flag, required=True, type=Path, help=description)
    cli.add_argument("--limit-ms", type=int, required=True)
    cli.add_argument("--timeout-ms", type=int, default=120000)
    cli.add_argument("--poll-ms", type=int, default=5)
    args = cli.parse_args()
    try:
        result = run(args.payload.resolve(), args.selection, args.run, args.policy, args.elf.resolve(),
                     args.stdin, args.out.resolve(), args.limit_ms, args.timeout_ms, args.poll_ms)
    except OSError as error:
        result = {"schema": 1, "authority": "diagnostic_only", "status": "unavailable", "pid": None, "reason": str(error)}
    print(json.dumps(result))
    raise SystemExit(live.EXIT[result["status"]])
