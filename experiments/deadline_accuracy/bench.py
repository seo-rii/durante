"""Exercise two clocks on each preregistered deadline, then collect independent pinned native CPU references."""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import platform
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from cpp20_locality.verify import require, sha
from deadline_accuracy import gate
from deadline_accuracy.registration import ARMS, registration_for
from first_run_sampler.profile import elf_digest
from guarded_native import live
from shape_calibration import combined, run, select_policy
from shape_calibration.bench import native_rounds, pinned_cpu, write


def deadlines(prediction_ns, percent, fixed):
    divisor = 100_000_000
    relative = [max(1, (prediction_ns * n + divisor - 1) // divisor) for n in percent]
    return sorted(set(fixed).union(relative))


def capture_case(payload, binaries, probe, out, case, seq, bank, probe_sha):
    key = case["case_id"]
    elf = binaries / case["program"]
    require(elf_digest(elf) == case["binary_sha256"], "unexpected workload ELF")
    stdin = out / "inputs" / sha(case["stdin"].encode())
    stdin.write_bytes(case["stdin"].encode())
    folder = out / "cases" / key
    folder.parent.mkdir(exist_ok=True)
    profile, _ = combined.capture(payload, probe, elf, stdin, folder)
    require(profile["stdout_sha256"] == sha(case["stdout"].encode()), "profile stdout differs")
    selection = select_policy.resolve(elf, folder, [out / "cases" / k for k in bank], probe_sha, "footprint")
    if selection["action"] == "calibrate":
        selected = key
        bank.append(key)
    else:
        selected = bank[selection["reference_index"]]
    write(folder / "baseline-selection.json", selection)
    return dict(sequence=seq, case_id=key, profile_pid=profile["launcher_pid"],
                profile_cpu_ns=profile["cpu_us"] * 1000, anchor_case=selected,
                baseline_ns=selection["prediction_ns"], limits=[])


def enforce_limits(payload, binaries, out, case, row, plan, strategy, packet_dir, *, batch_manifest=None):
    candidate = ARMS[strategy]
    key = case["case_id"]
    elf = binaries / case["program"]
    folder = out / "cases" / key
    stdin = out / "inputs" / sha(case["stdin"].encode())
    baseline_file = folder / "baseline-selection.json"
    paid = out / "cases" / row["anchor_case"] / "policy.json"
    for method in ("baseline", candidate):
        (folder / "limits" / method).mkdir(parents=True)
    limits = deadlines(row["baseline_ns"], plan["deadline_percent"], case["fixed_limits_ms"])
    for index, ms in enumerate(limits):
        chosen = gate.prepare(elf, stdin, folder, baseline_file, paid, packet_dir, ms, strategy=strategy,
                              batch_manifest=batch_manifest)
        order = ("baseline", candidate) if (row["sequence"] + index) % 2 == 0 else (candidate, "baseline")
        for method in order:
            decision, policy = (baseline_file, paid) if method == "baseline" else (chosen["selection"], chosen["policy"])
            dest = folder / "limits" / method / f"{index:02d}"
            result = run.run(payload, decision, folder, policy, elf, stdin, dest, ms,
                             plan["timeout_ms"], plan["poll_ms"])
            require(result["status"] in ("within_budget", "budget_exceeded"),
                    f"uncertified {key} deadline {ms}: {result.get('reason', result['status'])}")
            event = dict(index=index, limit_ms=ms, method=method, near=chosen["near"],
                         selection_path=str(decision.relative_to(out)), policy_path=str(policy.relative_to(out)),
                         exit_code=live.EXIT[result["status"]])
            if batch_manifest is not None and method == candidate:
                event["calibration_window"] = chosen["calibration_window"]
            row["limits"].append(event)


def bench(payload, binaries, probe, out, exact=False, contest=False, strategy=None, *, study=None):
    strategy = strategy or ("exact-one" if exact else "near-three")
    raw_plan, raw_rules, plan = registration_for(strategy, contest, study=study)
    probe_sha = elf_digest(probe)
    require(probe_sha == plan["probe_sha256"] == json.loads(raw_rules)["probe_sha256"], "different registered probe")
    affinity, allowed = pinned_cpu()
    sys.path.insert(0, str(payload))
    import cost_bench as runtime
    timings = {"trial_timeout_s": plan["timeout_ms"] // 1000,
               "work_budget": 100_000_000_000, "poll_interval_ms": plan["poll_ms"]}
    out.mkdir(mode=0o700)
    for name in ("inputs", "native", "cases"):
        (out / name).mkdir()
    banks = defaultdict(list)
    rows = []
    start = runtime.base["steal"](1)
    for seq, case in enumerate(plan["cases"]):
        bank = [] if strategy in ("warm-three", "interleaved-three") else banks[case["program"]]
        row = capture_case(payload, binaries, probe, out, case, seq, bank, probe_sha)
        rows.append(row)
        if strategy != "interleaved-three" and case["split"] == "holdout":
            enforce_limits(payload, binaries, out, case, row, plan, strategy,
                           out / "cases" / case["case_id"] / "deadline-packet")
    if strategy == "interleaved-three":
        from contest_batch.batch import prepare
        items = [dict(id=c["case_id"], elf=binaries / c["program"],
                      stdin=out / "inputs" / sha(c["stdin"].encode()), capture=out / "cases" / c["case_id"])
                 for c in plan["cases"]]
        prepare(items, out / "batch")
        manifest = out / "batch/manifest.json" if study == "contest_scale" else None
        for case, row in zip(plan["cases"], rows, strict=True):
            enforce_limits(payload, binaries, out, case, row, plan, "warm-three", out / "batch" / case["case_id"],
                           batch_manifest=manifest)
    native = native_rounds(plan, binaries, out, runtime, timings)
    write(out / "summary.json", dict(schema=1, plan_sha256=sha(raw_plan), rules_sha256=sha(raw_rules),
          probe_sha256=probe_sha, affinity=affinity, cgroup_cpus=allowed, kernel=platform.release(),
          steal_delta=runtime.base["steal"](1) - start, rows=rows, native=native))


def main(exact=False, contest=False, strategy=None, *, study=None):
    cli = argparse.ArgumentParser(description=__doc__)
    for flag in ("payload", "binaries", "probe", "out"):
        cli.add_argument("--" + flag, type=Path, required=True)
    args = cli.parse_args()
    bench(args.payload.resolve(), args.binaries.resolve(), args.probe.resolve(), args.out.resolve(), exact, contest, strategy, study=study)


if __name__ == "__main__":
    main()
