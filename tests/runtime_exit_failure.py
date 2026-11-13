#!/usr/bin/env python3
"""A failed runtime exit barrier must not invoke the terminal client callback."""
import argparse
import json
from pathlib import Path
import subprocess

from concurrent_spawn import cleanup_orphans, own_subreaper
from runtime_compat import InputProvenance, executable_path, run_process

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/runtime_exit_fixtures"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    sdk, work = args.sdk.resolve(), args.output_dir.absolute()
    work.mkdir(mode=0o700)
    prlimit = executable_path("prlimit")
    inputs = {"source:" + name: FIXTURES / name for name in ("app.c", "client.c", "CMakeLists.txt")}
    inputs.update({"harness": Path(__file__), "process_helper": ROOT / "tests/runtime_compat.py",
                   "cleanup_helper": ROOT / "tests/concurrent_spawn.py", "prlimit": prlimit,
                   "core": sdk / "lib64/release/libdynamorio.so", "drrun": sdk / "bin64/drrun",
                   "manifest": sdk / ".durante-runtime.json"})
    provenance = InputProvenance(inputs)
    provenance.capture("before")
    result = {"passed": False, "rows": [], "authority": "diagnostic_only"}
    owner = own_subreaper()
    try:
        if provenance.errors:
            raise ValueError("runtime exit fixture inputs are unavailable")
        commands = [
            ["gcc", "-O1", "-Wall", "-Wextra", "-Werror", "-rdynamic", "-pthread",
             str(FIXTURES / "app.c"), "-o", str(work / "app")],
            ["cmake", "-S", str(FIXTURES), "-B", str(work / "client"),
             "-DCMAKE_BUILD_TYPE=Release", "-DDynamoRIO_DIR=" + str(sdk / "cmake")],
            ["cmake", "--build", str(work / "client"), "-j2"],
        ]
        for index, command in enumerate(commands):
            with (work / f"build-{index}.log").open("wb") as log:
                subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=60)
        for name in ("join", "exit"):
            directory = work / name
            directory.mkdir(mode=0o700)
            ready, end, release = directory / "ready", directory / "end", directory / "release"
            command = [str(sdk / "bin64/drrun"), "-quiet", "-disable_traces",
                       "-vm_size", "32M", "-vmheap_size", "32M", "-stderr_mask", "12",
                       "-synch_all_threads_max_loops", "1", "-synch_with_sleep_time", "1",
                       "-c", str(work / "client/libexit_fixture.so"), str(ready), str(end), str(release),
                       "--", str(work / "app"), name, str(ready), str(release)]
            row = run_process(command, directory, 8000, prlimit)
            expected = 0 if name == "join" else 40
            row.update(name=name, expected_exit=expected, command=command,
                       ready=ready.is_file() and ready.read_bytes() == b"1\n",
                       terminal_callback=end.exists())
            row["passed"] = (row["infrastructure_ok"] and not row["errors"] and row["ready"]
                             and row["exit_code"] == expected and not row["stdout"]
                             and row["terminal_callback"] == (name == "join")
                             and (not row["stderr"] if name == "join" else
                                  "Failed to synchronize with all threads" in row["stderr"]))
            result["rows"].append(row)
        result["passed"] = len(result["rows"]) == 2 and all(row["passed"] for row in result["rows"])
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        result["error"] = str(error)[:1000]
    finally:
        result["cleanup_complete"] = cleanup_orphans()
        owner[0].prctl(36, owner[1], 0, 0, 0)
        provenance.capture("after")
        result["inputs_unchanged"] = provenance.unchanged()
        result["input_provenance"] = {"files": provenance.files, "errors": provenance.errors}
        result["passed"] = result["passed"] and result["cleanup_complete"] and provenance.unchanged()
        (work / "results.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({key: value for key, value in result.items() if key not in ("rows", "input_provenance")}))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
