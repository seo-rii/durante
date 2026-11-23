"""Calibrate a trusted, input-deterministic static ELF using pinned exact-input native observations."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import platform
import sys
import tempfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from first_run_sampler.profile import elf_digest, read_json, require, sha
from guarded_native import anchor, live
from shape_calibration import run, select_policy, temporal


# Indices are chronological. Warm-three retains the first receipt for accounting,
# then estimates CPU time from three subsequent processes on the same input.
VOTING_INDICES = {"near-three": (0, 1, 2), "exact-one": (0,), "warm-three": (1, 2, 3)}


def observation_count(strategy):
    require(strategy in VOTING_INDICES, "unknown exact-input calibration strategy")
    return VOTING_INDICES[strategy][-1] + 1


def near(prediction_ns, limit_ms):
    require(type(limit_ms) is int and limit_ms > 0 and type(prediction_ns) is int and prediction_ns >= 0,
            "expected positive whole-millisecond limit and nonnegative predicted nanoseconds")
    limit_ns = limit_ms * 1_000_000
    return 4 * abs(prediction_ns - limit_ns) <= limit_ns


def placement():
    affinity = sorted(os.sched_getaffinity(0))
    require(len(affinity) == 1, "pin the calibration to one CPU")
    return dict(cpu=affinity[0], kernel=platform.release(), machine=platform.machine(),
                boot_sha256=elf_digest(Path("/proc/sys/kernel/random/boot_id")))


def same_placement(native, context):
    return (native["single_cpu"] is True and native["affinity"] == [context["cpu"]]
            and native.get("boot_sha256") == context["boot_sha256"]
            and native["kernel"] == context["kernel"] and native["machine"] == context["machine"])


def original_is_current(policy, obs, context):
    return policy["anchor"] == obs and same_placement(policy["native"], context)


def packet_evidence(directory, obs, context, source_raw, strategy="near-three"):
    """Derive the manifest and normal selected-input receipt; offline callers can supply the recorded context."""
    samples = []
    hashes = []
    pids = set()
    count = observation_count(strategy)
    require({p.name for p in directory.glob("policy-*.json")} == {f"policy-{i}.json" for i in range(count)},
            "unexpected native policies for calibration strategy")
    for i in range(count):
        raw = (directory / f"policy-{i}.json").read_bytes()
        clock = anchor.clock_from_raw(raw, obs["elf_sha256"])
        policy = read_json(raw)
        native = policy["native"]
        require(policy["anchor"] == obs and same_placement(native, context),
                "native evidence belongs to a different capture or pinned context")
        require(native["pid"] != obs["pid"] and native["pid"] not in pids, "repeated native or capture PID")
        pids.add(native["pid"])
        hashes.append(clock.policy_sha256)
        samples.append(clock.native_ns)
    require(sha(source_raw) == context["source_policy_sha256"], "source policy changed")
    reused = original_is_current(read_json(source_raw), obs, context)
    if reused:
        require(hashes[0] == context["source_policy_sha256"], "first observation differs from paid current calibration")
    manifest = dict(schema=1, authority="diagnostic_only", context=context, reused_original=reused,
                    policy_sha256=hashes)
    votes = VOTING_INDICES[strategy]
    # A stable sort breaks CPU-time ties by their chronological packet index.
    index = sorted(votes, key=samples.__getitem__)[len(votes) // 2]
    if strategy == "near-three":
        manifest["median_index"] = index
    else:
        manifest.update(schema=2, strategy=strategy, selected_index=index)
    selected = dict(schema=1, method="footprint", action="calibrate", reference_index=None, comparisons=[],
                    policy_sha256=hashes[index], prediction_ns=samples[index], current_sample_sha256=obs["sample_sha256"])
    return manifest, selected


def exact_json(left, right):
    # Python equality identifies True with 1; the schema distinguishes them.
    return json.dumps(left, sort_keys=True) == json.dumps(right, sort_keys=True)


def prepare(elf, stdin, capture, selection, policy, packet_dir, limit_ms, strategy="near-three"):
    """Return selection and policy paths for one fresh enforcement. Share packet_dir across limits of one strategy."""
    count = observation_count(strategy)
    elf, stdin, capture, selection, policy = (Path(p).resolve() for p in (elf, stdin, capture, selection, policy))
    packet_dir = Path(packet_dir)
    origin = placement()
    selection_raw, source_raw = selection.read_bytes(), policy.read_bytes()
    selected, source = read_json(selection_raw), read_json(source_raw)
    clock = anchor.clock_from_raw(source_raw, anchor.static_elf(elf))
    require(selected["method"] == "footprint", "expected a footprint preflight")
    full, order = select_policy.profiles(capture, clock.elf_sha256, temporal.PROBE_SHA, selected["method"])
    obs = full["observation"]
    run.check_selection(selected, full, order, source, clock)
    require(elf_digest(stdin) == obs["stdin_sha256"], "requested input differs from the full capture")
    estimate = selected["prediction_ns"]
    is_near = near(estimate, limit_ms)
    if strategy == "near-three" and not is_near:
        return dict(near=False, selection=selection, policy=policy, prediction_ns=estimate, packet=None)
    context = dict(**origin, source_selection_sha256=sha(selection_raw), source_policy_sha256=clock.policy_sha256,
                   elf_sha256=clock.elf_sha256, stdin_sha256=obs["stdin_sha256"],
                   current_result_sha256=obs["result_sha256"])
    # Keep immutable packets across limits and processes. Failed writers only leave unpublished temporary data.
    # Lock the existing parent directory, avoiding stale lockfiles on failures or restarts.
    fd = os.open(packet_dir.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        if not packet_dir.exists():
            with tempfile.TemporaryDirectory(dir=packet_dir.parent) as tmp:
                draft = Path(tmp) / "packet"
                draft.mkdir(mode=0o700)
                for i in range(count):
                    dest = draft / f"policy-{i}.json"
                    if i == 0 and original_is_current(source, obs, context):
                        dest.write_bytes(source_raw)
                    else:
                        anchor.calibrate(elf, stdin, capture / "full", dest)
                manifest, candidate = packet_evidence(draft, obs, context, source_raw, strategy)
                anchor.save_new(draft / "packet.json", manifest)
                anchor.save_new(draft / "selection.json", candidate)
                draft.rename(packet_dir)
        manifest, candidate = packet_evidence(packet_dir, obs, context, source_raw, strategy)
        require(exact_json(read_json((packet_dir / "packet.json").read_bytes()), manifest)
                and exact_json(read_json((packet_dir / "selection.json").read_bytes()), candidate),
                "stored packet differs from current evidence")
        require(placement() == origin, "placement changed while obtaining the calibration")
    finally:
        os.close(fd)
    index = manifest["median_index"] if strategy == "near-three" else manifest["selected_index"]
    return dict(near=is_near, selection=packet_dir / "selection.json",
                policy=packet_dir / f"policy-{index}.json",
                prediction_ns=candidate["prediction_ns"], packet=packet_dir / "packet.json")


def enforce(payload, elf, stdin, capture, selection, policy, packet_dir, out, limit_ms, timeout_ms=120000,
            poll_ms=5, strategy="near-three"):
    out.mkdir(mode=0o700)
    try:
        require(all(type(v) is int and v > 0 for v in (limit_ms, timeout_ms, poll_ms)), "invalid deadline or watchdog")
        choice = prepare(elf, stdin, capture, selection, policy, packet_dir, limit_ms, strategy)
        receipt = dict(near=choice["near"], limit_ms=limit_ms,
                        prediction_ns=choice["prediction_ns"], source_selection_sha256=elf_digest(selection),
                        source_policy_sha256=elf_digest(policy), selected_policy_sha256=elf_digest(choice["policy"]),
                        packet_sha256=elf_digest(choice["packet"]) if choice["packet"] is not None else None)
        if strategy != "near-three":
            receipt["strategy"] = strategy
        anchor.save_new(out / "gate.json", receipt)
        result = run.run(payload, choice["selection"], capture, choice["policy"], elf, stdin, out / "enforced",
                         limit_ms, timeout_ms, poll_ms)
    except (OSError, ValueError, KeyError, TypeError, IndexError, OverflowError) as error:
        result = dict(schema=1, authority="diagnostic_only", status="unavailable", pid=None, reason=str(error))
    anchor.save_new(out / "result.json", result)
    return result


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    for flag in ("payload", "elf", "stdin", "capture", "selection", "policy", "packet-dir", "out"):
        cli.add_argument("--" + flag, required=True, type=Path)
    cli.add_argument("--limit-ms", required=True, type=int)
    cli.add_argument("--strategy", choices=VOTING_INDICES, default="near-three")
    cli.add_argument("--timeout-ms", type=int, default=120000)
    cli.add_argument("--poll-ms", type=int, default=5)
    args = cli.parse_args()
    try:
        result = enforce(args.payload.resolve(), args.elf.resolve(), args.stdin, args.capture, args.selection,
                         args.policy, args.packet_dir, args.out.resolve(), args.limit_ms, args.timeout_ms, args.poll_ms,
                         args.strategy)
    except (ValueError, OSError) as error:
        result = dict(schema=1, authority="diagnostic_only", status="unavailable", pid=None, reason=str(error))
    print(json.dumps(result))
    raise SystemExit(live.EXIT[result["status"]])
