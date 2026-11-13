#!/usr/bin/env python3
"""Bounded OS-failure injection in a separate, explicitly unqualified runtime."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

from concurrent_spawn import cleanup_orphans, own_subreaper
from runtime_compat import (InputProvenance, bounded_bytes, collect_records,
                            executable_path, run_process)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/runtime_exit_fixtures"
sys.path.insert(0, str(ROOT / "toolkit"))
import build_runtime as runtime

OPERATIONS = ("suspend", "context", "resume")
TEST_MANIFEST = ".durante-test-exit-faults.json"
CACHE_DIAGNOSTIC = "<Unknown processor cache size>\n"


def inject_source(source, fixture):
    """Instrument the real primitive call sites; reject an unfamiliar source."""
    replacements = {
        'extern vm_area_vector_t *fcache_unit_areas; /* from fcache.c */':
            fixture + '\nextern vm_area_vector_t *fcache_unit_areas; /* from fcache.c */',
        'if (!os_thread_suspend(trec)) {':
            'if (durante_test_exit_fault("suspend") || !os_thread_suspend(trec)) {',
        'if (!thread_get_mcontext(trec, &mc)) {':
            'if (durante_test_exit_fault("context") || !thread_get_mcontext(trec, &mc)) {',
        'if (at_safe_spot(trec, &mc, desired_state)) {':
            'if (!durante_test_exit_fault_selected("resume") &&\n'
            '                at_safe_spot(trec, &mc, desired_state)) {',
        'if (!os_thread_resume(trec)) {':
            'if (durante_test_exit_fault("resume") || !os_thread_resume(trec)) {',
        'dynamo_other_thread_exit(trec _IF_WINDOWS(false));':
            'dynamo_other_thread_exit(trec _IF_WINDOWS(false));\n'
            '            if (durante_test_exiting)\n'
            '                ++durante_test_cleaned;',
    }
    for old, new in replacements.items():
        if source.count(old) != 1:
            raise ValueError("fault injection source anchor is missing or ambiguous: " + old)
        source = source.replace(old, new, 1)
    return source


def build_test_sdk(sdk, work, source_archive, patch):
    """Copy inputs before mutation; never publish a production SDK manifest."""
    archive = runtime._get_archive(work, source_archive)
    snapshot = work / "source.tar.gz"
    shutil.copyfile(archive, snapshot)
    runtime._check_archive(snapshot)
    source = runtime._extract_source(snapshot, work)
    for name, input_path in (("runtime.patch", patch), ("replay.patch", runtime.REPLAY_PATCH)):
        copy = work / name
        shutil.copyfile(input_path, copy)
        subprocess.run(["patch", "--batch", "--forward", "--fuzz=0", "-p1", "-i", str(copy)],
                       cwd=source, check=True, timeout=30)
    path = source / "core/synch.c"
    path.write_text(inject_source(path.read_text(), (FIXTURES / "inject.c").read_text()))
    exit_path = source / "core/dynamo.c"
    text = exit_path.read_text()
    for old, new in (
        ("static bool dynamo_exiting = false;", "static bool dynamo_exiting = false;\n"
         "bool durante_test_exiting VAR_IN_SECTION(NEVER_PROTECTED_SECTION) = false;"),
        ("IF_UNIX(dynamo_exiting = true;) /* include execve-exited vfork threads */",
         "IF_UNIX(dynamo_exiting = true;) /* include execve-exited vfork threads */\n"
         "    durante_test_exiting = true;"),
    ):
        if text.count(old) != 1:
            raise ValueError("fault injection exit anchor is missing or ambiguous")
        text = text.replace(old, new, 1)
    exit_path.write_text(text)
    environment = os.environ.copy()
    for name in ("CFLAGS", "CXXFLAGS", "CPPFLAGS", "LDFLAGS"):
        environment.pop(name, None)
    built = runtime._compile_profiles(source, work, 2, environment)
    destination = work / "test-sdk"
    shutil.copytree(sdk, destination, symlinks=True)
    (destination / runtime.MANIFEST).unlink(missing_ok=True)
    for name, binary in built.items():
        target = destination / name
        target.unlink(missing_ok=True)
        if binary is not None:
            shutil.copyfile(binary, target)
            target.chmod(0o644 if name.endswith(".debug") else 0o755)
    marker = {"authority": "test_only", "production_qualified": False,
              "source_commit": runtime.COMMIT, "patch_sha256": runtime._hash_file(patch),
              "injection_sha256": runtime._hash_file(FIXTURES / "inject.c"),
              "injected_source_sha256": runtime._hash_file(path),
              "injected_exit_source_sha256": runtime._hash_file(exit_path),
              "artifacts": {name: runtime._hash_file(destination / name)
                            for name, binary in built.items() if binary is not None}}
    (destination / TEST_MANIFEST).write_text(json.dumps(marker, indent=2) + "\n")
    return destination


def verify_test_sdk(sdk, patch):
    if (sdk / runtime.MANIFEST).exists():
        raise ValueError("fault-injection SDK must not carry a production manifest")
    marker = json.loads(bounded_bytes(sdk / TEST_MANIFEST))
    if (marker.get("authority") != "test_only" or marker.get("production_qualified") is not False
            or marker.get("source_commit") != runtime.COMMIT
            or marker.get("patch_sha256") != runtime._hash_file(patch)
            or marker.get("injection_sha256") != runtime._hash_file(FIXTURES / "inject.c")):
        raise ValueError("fault-injection SDK source identity differs")
    artifacts = marker.get("artifacts", {})
    required = {"lib64/release/libdynamorio.so", "lib64/debug/libdynamorio.so"}
    if not required <= artifacts.keys() or not artifacts.keys() <= set(runtime.ARTIFACTS):
        raise ValueError("incomplete fault-injection SDK")
    for name, expected in artifacts.items():
        if runtime._hash_file(sdk / name) != expected:
            raise ValueError("fault-injection SDK artifact changed")


def observation_errors(row, measurement, events, operation, after, profile):
    errors = list(row["errors"])
    failing = operation in OPERATIONS
    if not row["infrastructure_ok"] or row["exit_code"] != (40 if failing else 0):
        errors.append("wrong process outcome or incomplete cleanup")
    if row["stdout"] or not row["ready"]:
        errors.append("fixture did not confirm both live workers")
    if sum(event.get("event") == "start" for event in events) != 1:
        errors.append("missing or ambiguous start record")
    terminal = sum(event.get("event") == "end" for event in events)
    if terminal != (0 if failing else 1):
        errors.append("terminal record disagrees with exit failure")
    diagnostic = row["stderr"]
    if profile == "debug" and diagnostic.startswith(CACHE_DIAGNOSTIC):
        diagnostic = diagnostic[len(CACHE_DIAGNOSTIC):]
    if failing:
        marker = f"<durante-test-exit-fault:{operation}:cleaned={after}>\n"
        if not diagnostic.startswith(marker):
            errors.append("selected OS failure was not injected after the requested cleanup")
        diagnostic = diagnostic.removeprefix(marker)
        if not re.fullmatch(r"<Application [^\n]+ \([0-9]+\)\. Failed to synchronize with all threads when detaching\.>\n", diagnostic):
            errors.append("unexpected fatal diagnostic")
        forbidden = ("instructions", "rep_bytes", "rep_entries", "unsupported_rep_entries",
                     "signals_observed", "processes", "segments", "process_count", "image_count")
        if (measurement.get("valid") is not False or measurement.get("complete") is not False
                or any(key in measurement for key in forbidden)):
            errors.append("failed exit exposed valid or partial accounting")
    else:
        if diagnostic:
            errors.append("unexpected control diagnostic")
        if (measurement.get("valid") is not True or measurement.get("complete") is not True
                or type(measurement.get("instructions")) is not int or measurement["instructions"] <= 0
                or measurement.get("process_count") != 1 or measurement.get("image_count") != 1):
            errors.append("control accounting incomplete")
    return errors


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("sdk", "client", "runner-bin", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--source-archive", type=Path)
    parser.add_argument("--build-dir", type=Path,
                        help="fresh build directory, separate from retained observation artifacts")
    parser.add_argument("--prepared-test-sdk", type=Path,
                        help="reuse a verified test-only SDK for a fresh bounded observation")
    parser.add_argument("--runtime-patch", type=Path, default=runtime.PATCH,
                        help="explicit baseline comparison input; never a production SDK")
    args = parser.parse_args(argv)
    for name in ("sdk", "client", "runner_bin", "output_dir", "runtime_patch"):
        setattr(args, name, getattr(args, name).resolve())
    work = args.output_dir
    work.mkdir(mode=0o700, parents=True)
    args.prlimit = executable_path("prlimit")
    inputs = {"harness": Path(__file__), "injection": FIXTURES / "inject.c",
              "application": FIXTURES / "fault_app.c", "runtime_builder": Path(runtime.__file__),
              "process_helper": ROOT / "tests/runtime_compat.py",
              "cleanup_helper": ROOT / "tests/concurrent_spawn.py",
              "patch": args.runtime_patch, "replay_patch": runtime.REPLAY_PATCH,
              "client": args.client, "runner": args.runner_bin, "prlimit": args.prlimit}
    provenance = InputProvenance(inputs)
    provenance.capture("before")
    result = {"passed": False, "rows": [], "authority": "diagnostic_only",
              "runtime_authority": "test_only", "failures": []}
    owner = own_subreaper()
    try:
        if provenance.errors:
            raise ValueError("fault injection inputs unavailable")
        build_dir = args.build_dir.resolve() if args.build_dir else work / "build"
        if not args.prepared_test_sdk:
            build_dir.mkdir(mode=0o700, parents=True)
        sdk = (args.prepared_test_sdk.resolve() if args.prepared_test_sdk else
               build_test_sdk(args.sdk, build_dir, args.source_archive, args.runtime_patch))
        verify_test_sdk(sdk, args.runtime_patch)
        artifact_inputs = {name: sdk / name for name in (TEST_MANIFEST, "bin64/drrun",
                            "lib64/release/libdynamorio.so", "lib64/debug/libdynamorio.so")}
        for name, path in artifact_inputs.items():
            inputs[name] = path
        compiled = work / "fault-app"
        subprocess.run(["gcc", "-O1", "-Wall", "-Wextra", "-Werror", "-pthread",
                        str(FIXTURES / "fault_app.c"), "-o", str(compiled)], check=True, timeout=30)
        inputs["compiled_application"] = compiled
        artifacts = InputProvenance({name: path for name, path in inputs.items()
                                     if name not in provenance.files})
        artifacts.capture("before")
        for profile in ("release", "debug"):
            cases = [("join", "none", 0), ("exit", "none", 0)]
            cases += [("exit", operation, after) for operation in OPERATIONS for after in (0, 1)]
            for mode, operation, after in cases:
                name = f"{profile}-{mode}-{operation}-{after}"
                directory = work / name
                directory.mkdir(mode=0o700)
                records = directory / "records"
                records.mkdir(mode=0o700)
                ready = directory / "ready"
                command = [executable_path("env"), "DURANTE_TEST_EXIT_FAILURE=" + operation,
                           "DURANTE_TEST_EXIT_AFTER=" + str(after), str(sdk / "bin64/drrun"),
                           *(["-debug"] if profile == "debug" else []),
                           "-quiet", "-disable_traces", "-vm_size", "32M", "-vmheap_size", "32M",
                           "-stderr_mask", "12", "-c", str(args.client), "-outdir", str(records),
                           "-rep_bytes", "--", str(compiled), mode, str(ready)]
                row = run_process(command, directory, 8000, args.prlimit)
                row.update(name=name, profile=profile, command=command,
                           ready=ready.is_file() and ready.read_bytes() == b"2\n")
                measurement = collect_records(args, directory, row["root_pid"])
                events = [json.loads(line) for path in records.glob("*.jsonl")
                          for line in bounded_bytes(path).splitlines()]
                errors = observation_errors(row, measurement, events, operation, after, profile)
                if bounded_bytes(directory / "collector-stderr.txt"):
                    errors.append("unexpected collector diagnostic")
                row.update(measurement=measurement, errors=errors, passed=not errors,
                           diagnostic_observations=(["cpu_cache_size_unknown"]
                               if profile == "debug" and row["stderr"].startswith(CACHE_DIAGNOSTIC)
                               else []),
                           terminal_records=sum(event.get("event") == "end" for event in events))
                result["rows"].append(row)
                if errors:
                    result["failures"].append(name)
        artifacts.capture("after")
        result["test_runtime_unchanged"] = artifacts.unchanged()
        result["test_runtime_provenance"] = {"files": artifacts.files, "errors": artifacts.errors}
        result["passed"] = len(result["rows"]) == 16 and not result["failures"] and artifacts.unchanged()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        result["failures"].append(str(error)[:1000])
    finally:
        result["cleanup_complete"] = cleanup_orphans()
        if owner[0].prctl(36, owner[1], 0, 0, 0):
            result["cleanup_complete"] = False
        provenance.capture("after")
        result["inputs_unchanged"] = provenance.unchanged()
        result["input_provenance"] = {"files": provenance.files, "errors": provenance.errors}
        result["passed"] &= result["cleanup_complete"] and result["inputs_unchanged"]
        (work / "results.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({key: result[key] for key in ("passed", "failures", "cleanup_complete", "inputs_unchanged")}))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
