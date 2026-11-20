#!/usr/bin/env python3
"""Check the online work page against real, independently collected terminal records."""
import argparse
import os
from pathlib import Path
import signal
import struct
import subprocess
import tempfile

from concurrent_spawn import cleanup_orphans, collector, own_subreaper
from jit_replay import FIXTURES
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


def check_run(prefix, work, name, command, *, valid=True, stdin=None):
    directory = work / name
    directory.mkdir()
    records = directory / "records"
    records.mkdir()
    page = directory / "live"
    page.write_bytes(bytes(PAGE.size))
    proc = subprocess.Popen([*prefix, "-outdir", str(records), "-rep_bytes", "-live_work_v1",
                             str(page), "--", *command], start_new_session=True, cwd=directory, stdin=stdin)
    try:
        if proc.wait(timeout=10) != 0:
            raise RuntimeError(f"{name}: fixture exited {proc.returncode}")
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
        if not cleanup_orphans():
            raise RuntimeError(f"{name}: failed to reap fixture")
    ready, invalid, instructions, movs, stos, overflow = PAGE.unpack(page.read_bytes())
    if (ready, invalid, overflow) != (1, int(not valid), 0):
        raise ValueError(f"{name}: unexpected ready/invalid/overflow {(ready, invalid, overflow)}")
    if valid:
        measurement = collector.collect(records, proc.pid)
        rep = measurement.get("rep_breakdown", {})
        actual = (instructions, movs, stos)
        expected = (measurement.get("instructions"), rep.get("movs_bytes"), rep.get("stos_bytes"))
        if not measurement.get("complete") or not measurement.get("valid") or actual != expected:
            raise ValueError(f"{name}: live {actual}, terminal {expected}, {measurement.get('reason')}")
        return measurement


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--drrun", required=True, type=Path)
    parser.add_argument("--client", required=True, type=Path)
    args = parser.parse_args()
    prefix = [str(args.drrun.resolve()), "-quiet", "-follow_children", "-disable_traces",
              "-vm_size", "32M", "-vmheap_size", "32M", "-c", str(args.client.resolve())]
    with tempfile.TemporaryDirectory(prefix="durante-live-work-") as scratch:
        work = Path(scratch)
        libc, previous_subreaper = own_subreaper()
        # Keep ordinary fork/vfork accounting eligible. A delivered SIGCHLD is
        # observable just like SIGUSR1; exercise that invalidation separately.
        previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGCHLD})
        try:
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
            check_run(prefix, work, "handled-signal", [tree, "signal"], valid=False)
            threads = work / "threads"
            subprocess.run(["cc", "-O2", "-pthread", ROOT / "tests/spawn_threads.c",
                            "-o", threads], check=True)
            check_run(prefix, work, "concurrent-spawn", [threads])
            active = work / "active"
            subprocess.run(["cc", "-O2", "-pthread", "-x", "c", "-", "-o", active],
                           text=True, input=ACTIVE_THREAD, check=True)
            check_run(prefix, work, "unfinished-thread", [active], valid=False)
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
            if libc.prctl(36, previous_subreaper, 0, 0, 0):
                raise RuntimeError("could not restore subreaper setting")
    print("live-work-client-ok")


if __name__ == "__main__":
    main()
