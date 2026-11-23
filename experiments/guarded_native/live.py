"""Stop a trusted, deterministic, single-process static ELF on its calibrated v11 clock."""
import argparse
import json
import os
from pathlib import Path
import runpy
import select
import signal
import stat
import struct
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from cpp20_locality.verify import require, verify_counters
from first_run_sampler.profile import elf_digest, read_json
from guarded_native.anchor import PROTOCOL, REFERENCE_SHA, load_clock, save_new

CLOCK = struct.Struct("<6Q")
ZERO = (0,) * 6
EXIT = {"within_budget": 0, "budget_exceeded": 123, "timed_out": 124, "unavailable": 1}


def verified_runtime(payload):
    raw = PROTOCOL.read_bytes()
    require(raw == (payload / "protocol.json").read_bytes(), "different v11 payload registration")
    hashes = read_json(raw)["tools_sha256"]
    for name, expected in hashes.items():
        if name.startswith("dynamorio/") or name in ("v11.so", "production_reader.py"):
            require(elf_digest(payload / name) == expected, "changed runtime file: " + name)
    return runpy.run_path(str(payload / "production_reader.py"))["collect"]


def snapshot(stream, path, previous):
    held = os.fstat(stream.fileno())
    require(stat.S_ISREG(held.st_mode) and held.st_nlink == 1 and held.st_size == CLOCK.size
            and os.path.samestat(held, path.lstat()), "live clock file changed")
    raw = os.pread(stream.fileno(), CLOCK.size, 0)
    require(len(raw) == CLOCK.size, "short live clock")
    words = CLOCK.unpack(raw)
    require(words == ZERO or words[:2] == (1, 0) and words[5] == 0, "invalid or overflowing live clock")
    require(all(a <= b for a, b in zip(previous, words)), "decreasing live clock")
    return words


def counts(words):
    return {"instructions": words[2], "movs_bytes": words[3], "stos_bytes": words[4],
            "rep_bytes": words[3] + words[4]}


def stop(child):
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    return child.wait()


def observe(child, clock_file, clock_path, calibrated, result):
    deadline = time.monotonic_ns() + result["timeout_ms"] * 1_000_000
    threshold = result["limit_ms"] * 1_000_000
    words = ZERO
    pidfd = os.pidfd_open(child.pid)
    try:
        while child.poll() is None:
            if time.monotonic_ns() >= deadline:
                if stop(child) == -signal.SIGKILL:
                    return "timed_out", None
                break  # Natural exit won the race. Reconcile its terminal records.
            words = snapshot(clock_file, clock_path, words)
            result["polls"] += 1
            if words[0] and calibrated.predict_ns(counts(words)) > threshold:
                if stop(child) == -signal.SIGKILL:
                    snapshot(clock_file, clock_path, words)  # Reject late invalidation or regression.
                    return "live", words
                break
            remaining = max(0, deadline - time.monotonic_ns()) / 1_000_000_000
            select.select([pidfd], [], [], min(remaining, result["poll_ms"] / 1000))
        require(child.returncode == 0, "target did not exit successfully")
        terminal = snapshot(clock_file, clock_path, words)
        require(terminal[0] == 1, "target exited without a live clock")
        return "terminal", terminal
    finally:
        os.close(pidfd)


def run(payload, policy, elf, stdin, out, limit_ms, timeout_ms=120000, poll_ms=5):
    out.mkdir(mode=0o700)
    result = dict(schema=1, authority="diagnostic_only", status="unavailable", observation=None,
                  prediction_ns=None, progress=None, pid=None, returncode=None, polls=0,
                  limit_ms=limit_ms, timeout_ms=timeout_ms, poll_ms=poll_ms,
                  reference_sha256=REFERENCE_SHA, policy_sha256=None, elf_sha256=None,
                  stdin_sha256=None, stdout_sha256=None)
    try:
        require(all(type(n) is int and n > 0 for n in (limit_ms, timeout_ms, poll_ms)),
                "limits and poll interval must be positive whole milliseconds")
        calibrated = load_clock(policy, elf)
        result.update(policy_sha256=calibrated.policy_sha256, elf_sha256=calibrated.elf_sha256,
                      anchor_native_ns=calibrated.native_ns)
        reader = verified_runtime(payload)
        input_copy = out / "stdin"
        input_copy.write_bytes(stdin.read_bytes())
        result["stdin_sha256"] = elf_digest(input_copy)
        records = out / "records"
        records.mkdir()
        clock_path = out / "clock.bin"
        require('"' not in str(out), "double quote in live clock path")
        options = f'-outdir "{records}" -rep_bytes -cpuid_profile x86-64-v2 -live_work_v1 "{clock_path}"'
        command = [payload / "dynamorio/bin64/drrun", "-quiet", "-client", payload / "v11.so", "00000000",
                   options, "-follow_children", "-disable_traces", "-vm_size", "32M", "-vmheap_size", "32M",
                   "--", elf]
        with (clock_path.open("x+b") as clock_file, input_copy.open("rb") as source,
              (out / "stdout").open("xb") as stdout, (out / "stderr").open("xb") as stderr):
            clock_file.truncate(CLOCK.size)
            start = time.monotonic_ns()
            with subprocess.Popen(command, stdin=source, stdout=stdout, stderr=stderr, start_new_session=True) as child:
                result["pid"] = child.pid
                try:
                    mode, words = observe(child, clock_file, clock_path, calibrated, result)
                finally:
                    if child.returncode is None:
                        stop(child)
                    result.update(returncode=child.returncode, wall_ns=time.monotonic_ns() - start)
            # A supported job has no surviving group member when its direct process is reaped.
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            else:
                raise ValueError("additional processes outlived the single-process job")
        require(elf_digest(elf) == calibrated.elf_sha256, "executable changed during execution")
        if mode == "timed_out":
            result["status"] = "timed_out"
        else:
            pid = result["pid"]
            record = records / f"process-{pid}.jsonl"
            require((out / "stderr").stat().st_size == 0 and set(records.iterdir()) == {record},
                    "stderr or extra process records in single-process job")
            if mode == "terminal":
                prod = reader(records, pid)
                # All three counters are independently bounded by uint64 in the v11 file.
                verify_counters(record.read_bytes(), prod, CLOCK.pack(*words), "live", pid, 3 * (2**64 - 1), reader)
                save_new(out / "production.json", prod)
            else:
                # A forced stop can leave just the start event. Tie that prefix to this launch.
                with record.open("rb") as events:
                    start_event = read_json(events.readline())
                require(start_event["pid"] == pid and start_event["event"] == "start"
                        and start_event["origin"] == "initial" and start_event["seq"] == 1
                        and start_event["authority"] == "diagnostic_only" and start_event["cpuid_profile"] == "x86-64-v2"
                        and start_event["rep_bytes_enabled"] is True and start_event["rep_breakdown_version"] == 1,
                        "invalid v11 start record for live crossing")
                (out / "crossing.bin").write_bytes(CLOCK.pack(*words))
            progress = counts(words)
            ns = calibrated.predict_ns(progress)
            result.update(status="budget_exceeded" if ns > limit_ms * 1_000_000 else "within_budget",
                          observation=mode, prediction_ns=ns, progress=progress)
    except (OSError, ValueError, KeyError, TypeError, OverflowError) as error:
        result["reason"] = str(error)
    if (out / "stdout").is_file():
        result["stdout_sha256"] = elf_digest(out / "stdout")
    save_new(out / "result.json", result)
    return result


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    for flag, description in (("policy", "native anchor from anchor.py calibrate"), ("elf", "immutable trusted static ELF"),
                              ("stdin", "input for this execution"), ("payload", "registered #82 runtime payload"),
                              ("out", "new output directory")):
        cli.add_argument("--" + flag, type=Path, required=True, help=description)
    cli.add_argument("--limit-ms", type=int, required=True)
    cli.add_argument("--timeout-ms", type=int, default=120000)
    cli.add_argument("--poll-ms", type=int, default=5)
    args = cli.parse_args()
    value = run(args.payload.resolve(), args.policy, args.elf.resolve(), args.stdin, args.out.resolve(),
                args.limit_ms, args.timeout_ms, args.poll_ms)
    print(json.dumps(value))
    raise SystemExit(EXIT[value["status"]])
