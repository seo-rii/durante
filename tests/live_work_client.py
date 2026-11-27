#!/usr/bin/env python3
"""Check the online work page against real, independently collected terminal records."""
import argparse
import json
import os
from pathlib import Path
import select
import signal
import struct
import subprocess
import tempfile

from concurrent_spawn import cleanup_orphans, collector, own_subreaper
from jit_replay import FIXTURES
from online_work_clock import ELIGIBLE
from rep_breakdown import CASES

ROOT = Path(__file__).resolve().parents[1]
PAGE = struct.Struct("<6Q")
# A live sibling can stop in the middle of a BB when the main thread returns.
ACTIVE_THREAD = """
#include <pthread.h>
#include <stdatomic.h>
#include <unistd.h>
static atomic_int active;
static void *worker(void *arg) {
    atomic_store(&active, 1);
    for (;;) pause();
    return arg;
}
int main(void) {
    pthread_t thread;
    if (pthread_create(&thread, NULL, worker, NULL)) return 3;
    while (!atomic_load(&active)) {}
    return 0;
}
"""


TIMEOUT = 10
# Client options every run adds after its live page (see --latency-bound).
CLIENT_OPTIONS = []


def check_run(prefix, work, name, command, *, valid=True, stdin=None, require_eligible=False,
              checkpoints=False):
    directory = work / name
    directory.mkdir()
    records = directory / "records"
    records.mkdir()
    page = directory / "live"
    page.write_bytes(bytes(PAGE.size))
    policy = ["-live_work_require_eligible"] if require_eligible else []
    proc = subprocess.Popen([*prefix, "-outdir", str(records), "-rep_bytes", "-live_work_v1",
                             str(page), *policy, *CLIENT_OPTIONS, "--", *command],
                            start_new_session=True, cwd=directory,
                            stdin=subprocess.PIPE if checkpoints else stdin,
                            stdout=subprocess.PIPE if checkpoints else None)
    try:
        if checkpoints:
            for invalid in (0, int(not valid)):
                if not select.select([proc.stdout], [], [], TIMEOUT)[0] or len(proc.stdout.read(1)) != 1:
                    raise ValueError(f"{name}: missing fixture checkpoint")
                ready, observed, *_, overflow = PAGE.unpack(page.read_bytes())
                if (ready, observed, overflow) != (1, invalid, 0):
                    raise ValueError(f"{name}: expected invalid={invalid}, got {ready, observed, overflow}")
                proc.stdin.write(b"x")
                proc.stdin.flush()
        if proc.wait(timeout=TIMEOUT) != 0:
            raise RuntimeError(f"{name}: fixture exited {proc.returncode}")
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
        if checkpoints:
            proc.stdin.close()
            proc.stdout.close()
        if not cleanup_orphans():
            raise RuntimeError(f"{name}: failed to reap fixture")
    return compare_page(name, page, records, proc.pid, valid)


def compare_page(name, page, records, root_pid, valid=True):
    """A usable page must hold exactly the terminal record's three counts."""
    ready, invalid, instructions, movs, stos, overflow = PAGE.unpack(page.read_bytes())
    if (ready, invalid, overflow) != (1, int(not valid), 0):
        raise ValueError(f"{name}: unexpected ready/invalid/overflow {(ready, invalid, overflow)}")
    measurement = collector.collect(records, root_pid)
    if not measurement.get("complete") or not measurement.get("valid"):
        raise ValueError(f"{name}: invalid terminal measurement: {measurement.get('reason')}")
    if "-latency_bound" in CLIENT_OPTIONS:
        bound = measurement.get("latency_bound") or {}
        if bound.get("version") != 1 or not bound.get("cycles_x16", -1) >= 4 * measurement["instructions"]:
            raise ValueError(f"{name}: latency bound missing or below issue cost: {bound}")
    if valid:
        rep = measurement.get("rep_breakdown", {})
        actual = (instructions, movs, stos)
        expected = (measurement.get("instructions"), rep.get("movs_bytes"), rep.get("stos_bytes"))
        if actual != expected:
            raise ValueError(f"{name}: live {actual}, terminal {expected}")
    return measurement


def symbols(path, names):
    found = {}
    for line in subprocess.run(["nm", path], capture_output=True, text=True, check=True).stdout.splitlines():
        fields = line.split()
        if len(fields) == 3 and fields[2] in names:
            found[fields[2]] = int(fields[0], 16)
    if set(found) != set(names):
        raise ValueError(f"{path} lacks symbols {sorted(set(names) - set(found))}")
    return found


def client_addresses(client):
    """Link-time addresses the debugger needs to find the client code it stops in."""
    image = client.read_bytes()
    if image[:6] != b"\x7fELF\x02\x01":
        raise ValueError("the client is not a little-endian ELF64 file")
    offset, size, count = struct.unpack_from("<Q", image, 32)[0], *struct.unpack_from("<HH", image, 54)
    loads = []
    for index in range(count):
        kind, _, file_offset, address, _, file_size = struct.unpack_from("<IIQQQQ", image, offset + index * size)
        if kind == 1:  # PT_LOAD
            loads.append((address, file_offset, file_size))
    found = symbols(client, ("charge_boundary_state", "boundary_routine", "observe_rep"))
    callback = found["charge_boundary_state"]
    address, file_offset, _ = next(load for load in loads if load[0] <= callback < load[0] + load[2])
    start = file_offset + callback - address
    return {"BASE": min(load[0] for load in loads) & ~0xfff, "CALLBACK": callback,
            "ROUTINE": found["boundary_routine"], "REP": found["observe_rep"]}, image[start:start + 16]


BOUNDARY_POINTS = ("entry", "switched", "call", "callback", "returned", "resume")


def check_boundary_signal(gdb, prefix, work, client, exe, point, name):
    """A signal that finds the thread at this point of client code is handled
    without waiting for the thread to leave the code cache: every fixture exits
    0 only if its handler ran, and the wait fixtures only if it ran within two
    iterations of the send. The state fixture also checks every register."""
    directory = work / name
    directory.mkdir()
    records, page, result = directory / "records", directory / "live", directory / "result.json"
    records.mkdir()
    page.write_bytes(bytes(PAGE.size))
    addresses, text = client_addresses(client)
    environment = dict(os.environ, DURANTE_GDB_CLIENT=str(client), DURANTE_GDB_POINT=point,
                       DURANTE_GDB_RESULT=str(result), DURANTE_GDB_CALLBACK_BYTES=text.hex(),
                       **{"DURANTE_GDB_" + key: hex(value) for key, value in addresses.items()})
    if exe.name.startswith("signal-wait"):
        mark = symbols(exe, ("count", "sent"))
        environment.update(DURANTE_GDB_COUNT=hex(mark["count"]), DURANTE_GDB_SENT=hex(mark["sent"]))
    target = [*prefix, "-outdir", str(records), "-rep_bytes", "-live_work_v1", str(page),
              *CLIENT_OPTIONS, "--", exe]
    # A library path meant for the target can replace the libpython the
    # debugger is linked with, as setup-python's does in CI. Keep it from the
    # debugger and give it back to the target.
    library_path = environment.pop("LD_LIBRARY_PATH", None)
    if library_path is not None:
        target = ["env", "LD_LIBRARY_PATH=" + library_path, *target]
    transcript = directory / "debugger.log"
    # The debugger waits for its inferior with SIGCHLD, which main() blocks.
    with transcript.open("wb") as output:
        proc = subprocess.Popen(
            [gdb, "-nx", "-batch", "-x", ROOT / "tests/live_boundary_gdb.py", "--args", *target],
            start_new_session=True, cwd=directory, env=environment,
            preexec_fn=lambda: signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGCHLD}),
            stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT)
    try:
        proc.wait(timeout=4 * TIMEOUT)
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
        if not cleanup_orphans():
            raise RuntimeError(f"{name}: failed to reap fixture")
    if result.is_file():
        report = json.loads(result.read_text())
    else:
        report = {"error": "no debugger report", "debugger_status": proc.returncode,
                  "debugger_output": transcript.read_bytes()[-2000:].decode(errors="replace")}
    if report.get("error") or report["stopped"] != report["target"] or report["exit_code"] != 0:
        raise RuntimeError(f"{name}: {report}")
    # Only the routine's first and last instructions run on the application's
    # stack; between them, and in the callback, the thread is on its private one.
    if report["application_stack"] != (point in ("entry", "resume")):
        raise RuntimeError(f"{name}: unexpected stack {report}")
    # The handled signal leaves the page usable: it matches the terminal record.
    (root,) = {json.loads(line)["pid"] for path in records.iterdir()
               for line in path.read_text().splitlines()
               if json.loads(line).get("parent_pid") == 0}
    if not compare_page(name, page, records, root)["signals_observed"]:
        raise ValueError(f"{name}: no signal observed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--drrun", required=True, type=Path)
    parser.add_argument("--client", required=True, type=Path)
    parser.add_argument("--engine-debug", action="store_true",
                        help="use the SDK's debug core, whose exit checks fail a run that leaks")
    parser.add_argument("--gdb", type=Path,
                        help="also deliver a signal at each point of the live boundary routine")
    parser.add_argument("--latency-bound", action="store_true",
                        help="charge the static latency bound too, which live entries charge inline")
    args = parser.parse_args()
    global TIMEOUT
    if args.engine_debug:
        TIMEOUT = 60
    if args.latency_bound:
        CLIENT_OPTIONS.append("-latency_bound")
    prefix = [str(args.drrun.resolve()), *(["-debug"] if args.engine_debug else []),
              "-quiet", "-follow_children", "-disable_traces",
              "-vm_size", "32M", "-vmheap_size", "32M", "-c", str(args.client.resolve())]
    with tempfile.TemporaryDirectory(prefix="durante-live-work-") as scratch:
        work = Path(scratch)
        libc, previous_subreaper = own_subreaper()
        # Keep ordinary fork/vfork accounting eligible. A delivered SIGCHLD is
        # observable just like SIGUSR1; exercise that invalidation separately.
        previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGCHLD})
        try:
            eligibility = work / "eligibility"
            subprocess.run(["cc", "-static", "-nostdlib", "-no-pie",
                            ROOT / "tests/live_work_eligibility.S", "-o", eligibility], check=True)
            for mode, quality in (("none", (0, 0)), ("signal", (1, 0)),
                                  ("cmps", (0, 1)), ("repne-scas", (0, 1))):
                for strict in (False, True):
                    name = f"eligibility-{mode}-{'strict' if strict else 'raw'}"
                    result = check_run(prefix, work, name, [eligibility, mode], checkpoints=True,
                                       require_eligible=strict, valid=not (strict and any(quality)))
                    observed = (result["signals_observed"], result["unsupported_rep_entries"])
                    if observed != quality:
                        raise ValueError(f"{name}: expected terminal quality {quality}, got {observed}")
            boundaries = work / "clock-boundaries"
            subprocess.run(["cc", "-static", "-nostdlib", "-no-pie",
                            ROOT / "experiments/l1d_probe/clock_control.S", "-o", boundaries], check=True)
            for mode, argv in (("cold", []), ("warm", ["warm"])):
                check_run(prefix, work, f"clock-boundaries-{mode}", [boundaries, *argv])
            input_clock = work / "clock-input"
            subprocess.run(["cc", "-static", "-nostdlib", "-no-pie",
                            ROOT / "tests/clock_input.S", "-o", input_clock], check=True)
            input_file = work / "input"
            for mode in ("r", "v", "e", "x"):
                input_file.write_bytes(b"" if mode == "e" else b"A")
                with input_file.open("rb") as stdin:
                    measurement = check_run(prefix, work, f"input-{mode}", [input_clock, mode], stdin=stdin)
                # Three setup instructions, 12,000 two-instruction iterations,
                # and 33 instructions through the read, checks and exit.
                if measurement["instructions"] != 24_036:
                    raise ValueError(f"unexpected input clock for {mode}: {measurement['instructions']}")
            state = work / "live-state-exe"
            subprocess.run(["cc", "-static", "-nostdlib", "-no-pie",
                            ROOT / "tests/live_state.S", "-o", state], check=True)
            # The loop alone is 3 * 3,000,000 - 1 instructions, so the run
            # crosses more than two thousand publication boundaries.
            if not 8_999_999 < check_run(prefix, work, "live-state", [state])["instructions"] < 9_001_000:
                raise ValueError("unexpected live state clock")
            # The same state with a signal handler that overwrites all of it:
            # first interrupted by a timer, then by one signal at each point of
            # the boundary routine, which no timer can aim at.
            for name, mode in (("timer", 1), ("external", 2)):
                subprocess.run(["cc", "-static", "-nostdlib", "-no-pie", f"-DLIVE_STATE_SIGNALS={mode}",
                                ROOT / "tests/live_state.S", "-o", work / f"live-state-{name}-exe"],
                               check=True)
            if not check_run(prefix, work, "live-state-timer", [work / "live-state-timer-exe"])["signals_observed"]:
                raise ValueError("live-state-timer: no signal observed")
            if args.gdb:
                for name, defines in (("signal-wait-exe", []), ("signal-wait-rep-exe", ["-DSIGNAL_WAIT_REP"])):
                    subprocess.run(["cc", "-static", "-nostdlib", "-no-pie", *defines,
                                    ROOT / "tests/signal_wait.S", "-o", work / name], check=True)
                for point in BOUNDARY_POINTS:
                    check_boundary_signal(args.gdb, prefix, work, args.client.resolve(),
                                          work / "live-state-external-exe", point,
                                          f"boundary-signal-{point}")
                    check_boundary_signal(args.gdb, prefix, work, args.client.resolve(),
                                          work / "signal-wait-exe", point, f"boundary-wait-{point}")
                # A runtime clean call: the REP callee runs on the runtime's stack.
                check_boundary_signal(args.gdb, prefix, work, args.client.resolve(),
                                      work / "signal-wait-rep-exe", "rep-callee", "rep-callee-wait")
            # Each .Lwork body takes 5*N+18 instructions. Reading stdin adds
            # 12 instructions and a two-instruction loop with 10,000 iterations.
            thread_work = 5 * (100003 + 70001) + 2 * 18 + 32
            read_work = 12 + 2 * 10000
            # The fork parent also runs .Lwork(1009) before the syscall. It
            # executes 4 + 3 + 16 instructions outside both calls, the child 3 + 5.
            fork_parent = 5 * (1009 + 100003) + 2 * (18 + read_work) + 23
            fork_child = 5 * 70001 + 18 + read_work + 8
            for name, defines, segments in (
                    ("thread-clock", [], [(2, thread_work)]),
                    ("thread-clock-input", ["-DCLOCK_READ_STDIN"], [(2, thread_work + 2 * read_work)]),
                    ("fork-clock-input", ["-DCLOCK_READ_STDIN", "-DCLOCK_FORK"],
                     [(1, fork_parent), (1, fork_child)])):
                exe = work / (name + "-exe")
                subprocess.run(["cc", "-static", "-nostdlib", "-no-pie", *defines,
                                ROOT / "tests/clock_threads.S", "-o", exe], check=True)
                input_file.write_bytes(b"XYZ")
                with input_file.open("rb") as stdin:
                    measurement = check_run(prefix, work, name, [exe], stdin=stdin)
                root_pid = next(p["pid"] for p in measurement["processes"] if p["parent_pid"] == 0)
                ordered = sorted(measurement["segments"], key=lambda row: row["pid"] != root_pid)
                actual = [(row["threads"], row["instructions"]) for row in ordered]
                if actual != segments or measurement["instructions"] != sum(count for _, count in segments):
                    raise ValueError(f"unexpected {name} clock: {measurement}")
            for fixture, flag, modes in [
                ("rep_breakdown", "MODE", {name: row[0] for name, row in CASES.items()}),
                ("jit_replay", "CASE", {name: row["case"] for name, row in FIXTURES.items()}),
            ]:
                for name, mode in modes.items():
                    exe = work / f"{fixture}-{name}"
                    subprocess.run(["cc", "-static", "-nostdlib", "-no-pie", f"-D{flag}={mode}",
                                    ROOT / "tests" / (fixture + ".S"), "-o", exe], check=True)
                    check_run(prefix, work, exe.name + "-run", [exe])
            tree = work / "tree"
            subprocess.run(["cc", "-O2", ROOT / "tests/tree_fixture.c", "-o", tree], check=True)
            for mode in ("nested", "exec-chain", "vfork-exit", "vfork-failed-exec", "posix-spawn"):
                check_run(prefix, work, mode, [tree, mode])
            # The terminal record accepts a handled signal, so the page stays usable.
            measurement = check_run(prefix, work, "handled-signal", [tree, "signal"])
            if not measurement["signals_observed"]:
                raise ValueError(f"handled-signal: no signal observed: {measurement}")
            threads = work / "threads"
            subprocess.run(["cc", "-O2", "-pthread", ROOT / "tests/spawn_threads.c",
                            "-o", threads], check=True)
            check_run(prefix, work, "concurrent-spawn", [threads])
            active = work / "active"
            subprocess.run(["cc", "-O2", "-pthread", "-x", "c", "-", "-o", active],
                           text=True, input=ACTIVE_THREAD, check=True)
            # A sibling that exit_group stops, waiting in a syscall or running in
            # a block, publishes the same remainder its terminal record counts.
            check_run(prefix, work, "unfinished-thread", [active])
            eligible = work / "eligible"
            subprocess.run(["cc", "-O2", "-pthread", "-x", "c", "-", "-o", eligible],
                           text=True, input=ELIGIBLE, check=True)
            check_run(prefix, work, "running-thread", [eligible, "thread"])
            # A child does not publish the copy of a sibling's state it inherits.
            check_run(prefix, work, "fork-running-thread", [eligible, "fork"])
            measurement = check_run(prefix, work, "scas", [eligible, "scas"])
            if not measurement["unsupported_rep_entries"]:
                raise ValueError(f"scas: no CMPS/SCAS entry observed: {measurement}")
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
            if libc.prctl(36, previous_subreaper, 0, 0, 0):
                raise RuntimeError("could not restore subreaper setting")
    print("live-work-client-ok")


if __name__ == "__main__":
    main()
