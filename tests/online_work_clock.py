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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("runner", "drrun", "client"):
        parser.add_argument("--" + key, type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        for filename, source in (("three.S", EXIT_THREE), ("small.S", SMALL_WORK_THEN_WAIT),
                                 ("work.c", WORKLOAD)):
            path = root / filename
            path.write_text(source)
            flags = ["-nostdlib", "-no-pie"] if path.suffix == ".S" else ["-O2"]
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


if __name__ == "__main__":
    main()
