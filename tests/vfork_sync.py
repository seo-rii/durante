#!/usr/bin/env python3
"""Exercise a cache reset while clone/clone3 has a parent blocked in vfork."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess

from concurrent_spawn import bounded_number, cleanup_orphans, own_subreaper
from runtime_workloads import Workload, run_case

ROOT = Path(__file__).resolve().parents[1]
CALLS = ("clone", "clone3")
ACTIONS = ("exit", "exec", "failed-exec", "failed-clone")
MODES = ("engine-only", "client-rep-off", "supervisor")
OUTPUT = "vfork-sync:child-ready\nvfork-sync:complete\n"


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def fixture(executable, call, action, native, name):
    return Workload(name, "native", [str(executable), call,
                    "native" if native else "engine", action], OUTPUT,
                    processes=2, images=3 if action == "exec" else 2,
                    minimum_threads=3)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("sdk", "output-dir"):
        parser.add_argument("--" + flag, type=Path, required=True)
    for flag in ("client", "runner-bin"):
        parser.add_argument("--" + flag, type=Path)
    parser.add_argument("--profiles", nargs="+", choices=("release", "debug"),
                        default=["release", "debug"])
    parser.add_argument("--modes", nargs="+", choices=MODES, default=list(MODES))
    parser.add_argument("--repeats", type=bounded_number(1, 10), default=1)
    parser.add_argument("--timeout-ms", type=bounded_number(1000, 60000), default=10000)
    args = parser.parse_args()
    if len(set(args.profiles)) != len(args.profiles) or len(set(args.modes)) != len(args.modes):
        parser.error("duplicate profiles or modes")
    if any(mode != "engine-only" for mode in args.modes) and (
            args.client is None or args.runner_bin is None):
        parser.error("measured modes require --client and --runner-bin")
    for flag in ("sdk", "output_dir", "client", "runner_bin"):
        value = getattr(args, flag)
        if value is not None:
            setattr(args, flag, value.resolve())
    args.prlimit = None
    os.umask(0o077)
    args.output_dir.mkdir(parents=True, mode=0o700, exist_ok=False)
    paths = [ROOT / "tests/vfork_sync.c", Path(__file__).resolve(),
             ROOT / "tests/runtime_workloads.py", ROOT / "tests/runtime_compat.py",
             ROOT / "tests/concurrent_spawn.py", ROOT / "collect.py",
             args.sdk / "bin64/drrun", args.sdk / ".durante-runtime.json"]
    paths += [args.sdk / "lib64" / profile / "libdynamorio.so" for profile in args.profiles]
    paths += [path for path in (args.client, args.runner_bin) if path is not None]
    before = {str(path): digest(path) for path in paths}
    (args.output_dir / "plan.json").write_text(json.dumps({
        "profiles": args.profiles, "modes": args.modes, "calls": CALLS,
        "actions": ACTIONS, "repeats": args.repeats, "timeout_ms": args.timeout_ms,
        "inputs": before}, indent=2) + "\n")
    executable = args.output_dir / "vfork-sync"
    subprocess.run(["gcc", "-std=c17", "-O2", "-Wall", "-Wextra", "-Werror", "-pthread",
                    str(paths[0]), "-o", str(executable)], check=True, umask=0o022)
    before[str(executable)] = digest(executable)
    rows, errors = [], []
    libc, previous_subreaper = own_subreaper()
    try:
        for call in CALLS:
            for action in ACTIONS:
                workload = fixture(executable, call, action, True, call + "-" + action)
                rows.append(run_case(args, workload, "native"))
        if all(row["passed"] for row in rows):
            for profile in args.profiles:
                # Both callers put the frontend -quiet first. Consume it here so
                # no frontend option appears after the runtime's -enable_reset.
                wrapper = args.output_dir / ("drrun-" + profile)
                prefix = [str(args.sdk / "bin64/drrun")]
                if profile == "debug":
                    prefix.append("-debug")
                # Keep errors/assertions visible without debug startup chatter,
                # matching the shared engine-only and REP-off workload profile.
                prefix += ["-quiet", "-enable_reset", "-stderr_mask", "12"]
                wrapper.write_text('#!/bin/sh\n[ "$1" = "-quiet" ] || exit 2\nshift\nexec '
                                   + shlex.join(prefix) + ' "$@"\n')
                wrapper.chmod(0o755)
                before[str(wrapper)] = digest(wrapper)
                args.drrun = wrapper
                for repeat in range(args.repeats):
                    for call in CALLS:
                        for action in ACTIONS:
                            name = f"{profile}-{repeat:02d}-{call}-{action}"
                            workload = fixture(executable, call, action, False, name)
                            for mode in args.modes:
                                row = run_case(args, workload, mode)
                                row.update(profile=profile, call=call, action=action, repeat=repeat)
                                rows.append(row)
        else:
            errors.append("native controls failed; this test requires working clone3")
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        errors.append(type(error).__name__ + ": " + str(error)[:500])
    finally:
        cleaned = False
        try:
            cleaned = cleanup_orphans()
        except OSError as error:
            errors.append("cleanup failed: " + str(error)[:500])
        finally:
            restored = libc.prctl(36, previous_subreaper, 0, 0, 0) == 0
    stable = all(path.is_file() and digest(path) == value
                 for name, value in before.items() for path in [Path(name)])
    expected = len(CALLS) * len(ACTIONS) * (1 + len(args.profiles) * len(args.modes) * args.repeats)
    failures = [row["workload"] + "/" + row["mode"] for row in rows if not row["passed"]]
    if len(rows) != expected:
        errors.append(f"incomplete matrix: {len(rows)} of {expected} rows")
    if not cleaned or not restored or not stable:
        errors.append("cleanup, subreaper restoration or input stability failed")
    result = {"exit_code": int(bool(errors or failures)), "rows": rows, "errors": errors,
              "failures": failures, "cases": len(rows), "expected_cases": expected,
              "passed": len(rows) - len(failures), "cleanup_complete": cleaned,
              "subreaper_restored": restored, "inputs_unchanged": stable, "inputs": before}
    (args.output_dir / "results.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({key: value for key, value in result.items() if key not in ("rows", "inputs")}))
    return result["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
