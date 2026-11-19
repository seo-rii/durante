"""Check the loop-only report against a hand-counted loop and the full client."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile


def observe(drrun, client, program, options, expected_error=None, runtime_flags="-no_cleancall_ignore_eflags"):
    with tempfile.TemporaryDirectory() as directory:
        report = Path(directory) / "features.json"
        command = [drrun, "-quiet", "-disable_traces", runtime_flags, "-c", client, "-out", report.name,
                   *options, "--", program]
        result = subprocess.run(command, cwd=directory, capture_output=True, text=True, timeout=20)
        if expected_error:
            if result.returncode != 1 or expected_error not in result.stderr or report.exists():
                raise AssertionError(f"expected rejection for {command}: {result}")
            return
        if result.returncode:
            raise AssertionError(f"{command} exited {result.returncode}: {result.stderr}")
        return json.loads(report.read_text())


def main(drrun, client, once, nine, threads):
    only = "-loop_recurrence_only"
    field = "loop_recurrence_cycles_x16"
    # The entry block includes the first load. The next eight executions of
    # the self-loop each carry a five-cycle load-to-address dependency.
    for program, iterations in ((once, 1), (nine, 9)):
        expected = (iterations - 1) * 5 * 16
        narrow = observe(drrun, client, program, [only])
        full = observe(drrun, client, program, [])
        if narrow != {"version": 1, field: expected} or full[field] != expected:
            raise AssertionError(f"{program}: expected {expected}, got {narrow} and {full}")
        if full["instructions"] != 5 + 3 * (iterations - 1) + 3:
            raise AssertionError(f"{program}: unexpected full-client instruction count {full}")

    for options in ([only, "-bpred"], ["-load_pattern", only]):
        observe(drrun, client, nine, options, "excludes -bpred and -load_pattern")
    observe(drrun, client, threads, [only], "requires a single application thread")
    observe(drrun, client, nine, [only], "requires full clean-call flag preservation",
            runtime_flags="-cleancall_ignore_eflags")


if __name__ == "__main__":
    main(*sys.argv[1:])
