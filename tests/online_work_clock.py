#!/usr/bin/env python3
"""Exercise the real run owner, client and prepared DynamoRIO core on Linux."""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile


# Omit dynamic startup so the equality boundary has an independent oracle.
EXIT_THREE = """.global _start
.text
_start:
    mov $60, %eax
    xor %edi, %edi
    syscall
"""
# The 403rd application instruction enters pause(). No BB follows it to publish
# another batch. Work after the blocking syscall must not enter this prefix.
SMALL_WORK_THEN_WAIT = """.global _start
.text
_start:
    mov $200, %ecx
1:  dec %ecx
    jnz 1b
    mov $34, %eax
    syscall
    mov $60, %eax
    xor %edi, %edi
    syscall
"""
WORKLOAD = """
#include <signal.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

int main(int argc, char **argv) {
    if (argc != 2) return 2;
    if (!strcmp(argv[1], "wait")) {
        pause();
        return 3;
    }
    if (!strcmp(argv[1], "child")) {
        pid_t child = fork();
        if (child < 0) return 4;
        if (child != 0) {
            pause();
            return 5;
        }
        printf("%d\\n", (int)getpid());
        fflush(stdout);
    }
    for (volatile unsigned long i = 0; ; ++i) {}
}
"""
# Conditions the terminal record accepts must leave the live page usable too.
ELIGIBLE = """
#include <pthread.h>
#include <signal.h>
#include <stdatomic.h>
#include <string.h>

static volatile sig_atomic_t handled;
static atomic_int running;
static void on_signal(int sig) { handled = sig; }
static void *spin(void *arg) {
    atomic_store(&running, 1);
    for (volatile unsigned long i = 0; ; ++i) {}
    return arg;
}

int main(int argc, char **argv) {
    if (argc != 2) return 2;
    if (!strncmp(argv[1], "signal", 6)) {
        signal(SIGUSR1, on_signal);
        raise(SIGUSR1);
        if (handled != SIGUSR1) return 3;
        if (!strcmp(argv[1], "signal-loop"))
            for (volatile unsigned long i = 0; ; ++i) {}
    } else if (!strcmp(argv[1], "thread")) {
        /* The sibling is inside a block when exit_group stops it. */
        pthread_t thread;
        if (pthread_create(&thread, NULL, spin, NULL)) return 4;
        while (!atomic_load(&running)) {}
    } else if (!strcmp(argv[1], "scas")) {
        static char text[4096];
        memset(text, 'a', sizeof(text) - 1);
        const char *at = text;
        unsigned long left = sizeof(text);
        __asm__ volatile("repne scasb" : "+D"(at), "+c"(left) : "a"(0) : "cc", "memory");
        if (at != text + sizeof(text)) return 5;
    } else {
        return 6;
    }
    return 0;
}
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("runner", "drrun", "client"):
        parser.add_argument("--" + key, type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        for filename, source in (("three.S", EXIT_THREE), ("small.S", SMALL_WORK_THEN_WAIT),
                                 ("work.c", WORKLOAD), ("eligible.c", ELIGIBLE)):
            path = root / filename
            path.write_text(source)
            flags = ["-nostdlib", "-no-pie"] if path.suffix == ".S" else ["-O2", "-pthread"]
            subprocess.run(["cc", "-static", *flags, str(path), "-o", str(path.with_suffix(""))], check=True)

        def run(name, budget, wall_ms, *program):
            report = root / (name + ".json")
            proc = subprocess.run([args.runner.resolve(), "run", "--drrun", args.drrun.resolve(),
                                   "--client", args.client.resolve(), "--timeout-ms", str(wall_ms),
                                   "--report", report, "--work-budget", str(budget), "--", *program],
                                  capture_output=True, text=True, timeout=wall_ms / 1000 + 10)
            data = json.loads(report.read_bytes())
            e = data["execution"]
            receipt = data["work_budget"]
            print(json.dumps({"case": name, "exit": proc.returncode, "execution": e,
                              "work_budget": receipt}), flush=True)
            assert e["cleanup_complete"] and e["records_cleanup_complete"], (data, proc.stderr)
            return proc, e, receipt, data["measurement"]

        proc, e, receipt, m = run("equal", 3, 5000, root / "three")
        assert (proc.returncode, e["state"], receipt["status"], m["instructions"]) == (0, "complete", "within_budget", 3)
        proc, e, receipt, m = run("one_over", 2, 5000, root / "three")
        assert (proc.returncode, e["state"], receipt["status"], receipt["instructions"]) == (123, "work_budget_exceeded", "exceeded", 3)
        proc, e, receipt, m = run("small_wait", 402, 3000, root / "small")
        assert (proc.returncode, e["state"], receipt.get("observation"),
                receipt.get("instructions")) == (123, "work_budget_exceeded", "live", 403)
        assert not m["complete"]
        proc, e, receipt, m = run("small_wait_equal", 403, 800, root / "small")
        assert (proc.returncode, e["state"], receipt["status"]) == (124, "timed_out", "unavailable")
        for mode in ("loop", "child"):
            proc, e, receipt, m = run(mode, 2_000_000, 15_000, root / "work", mode)
            assert (proc.returncode, e["state"], receipt["observation"]) == (123, "work_budget_exceeded", "live")
            assert e["work_budget_killed_processes"] > 0
            assert sum(receipt[k] for k in ("instructions", "movs_bytes", "stos_bytes")) > receipt["limit"]
            assert not m["complete"] and m.get("instructions") is None and e["wall_time_ms"] < 15_000
            if mode == "child":
                child = int(proc.stdout.strip())
                assert e["supervisor_reaped_processes"] >= 2 and not Path(f"/proc/{child}").exists()
        proc, e, receipt, m = run("wait", 2_000_000, 800, root / "work", "wait")
        assert (proc.returncode, e["state"], receipt["status"]) == (124, "timed_out", "unavailable")
        assert not m["complete"] and m.get("instructions") is None
        # A handled signal no longer switches the budget off for the rest of the run.
        proc, e, receipt, m = run("signal_loop", 2_000_000, 15_000, root / "eligible", "signal-loop")
        assert (proc.returncode, e["state"], receipt["observation"]) == (123, "work_budget_exceeded", "live")
        assert e["wall_time_ms"] < 15_000
        # A handled signal, a sibling stopped by exit_group and a CMPS/SCAS
        # entry each complete within budget with the terminal counts.
        for mode, field in (("signal", "signals_observed"), ("thread", None),
                            ("scas", "unsupported_rep_entries")):
            proc, e, receipt, m = run("eligible_" + mode, 10_000_000_000, 15_000,
                                      root / "eligible", mode)
            assert (proc.returncode, e["state"], receipt["status"], receipt["observation"]) == (
                0, "complete", "within_budget", "terminal"), (mode, e, receipt, m.get("reason"))
            assert m["complete"] and receipt["instructions"] == m["instructions"], (mode, m)
            assert field is None or m[field] > 0, (mode, m)


if __name__ == "__main__":
    main()
