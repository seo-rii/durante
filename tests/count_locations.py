#!/usr/bin/env python3
"""Locate raw-count variation in fixed CPython controls, without qualifying it."""
from collections import defaultdict
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from concurrent_spawn import bounded_number, cleanup_orphans, own_subreaper
from runtime_compat import (InputProvenance, bounded_bytes, collect_records,
                            executable_path, inspect_accounting, run_process)
from runtime_repeatability import (CPYTHON_CONTROL_FLAGS, PYTHON_STARTUP_CONTROLS,
                                   FIXED_ENV, collect_cpython_probe,
                                   fixed_environment, host_profile, publish)

ROOT = Path(__file__).resolve().parents[1]
WALL_MS = 30000
ROW_ALLOWANCE = 53  # Full wall limit plus collection/publication/cleanup.
CAMPAIGN_SECONDS = 300


class ModuleUnloaded(ValueError):
    def __init__(self, count):
        super().__init__("module unloaded during observation")
        self.count = count


def parse_profile(data, measured_instructions):
    if len(data) > 1048576 or not data.endswith(b"\n"):
        raise ValueError("profile size or termination")
    lines = [line.split("\t") for line in data.decode("ascii").splitlines()]
    if lines[0] != ["PROFILE", "3", "65536", "128", "1048320"]:
        raise ValueError("profile version or bounds")
    if len(lines[-1]) != 10 or lines[-1][0] != "END":
        raise ValueError("profile terminal missing")
    complete, stored, emitted, charge, rollback, replays, started, ended, unloads = map(int, lines[-1][1:])
    if not 0 <= unloads < 2**64:
        raise ValueError("invalid module unload counter")
    if unloads:
        raise ModuleUnloaded(unloads)
    if complete != 1 or stored != emitted or not 0 < emitted <= 65536:
        raise ValueError("profile incomplete")
    if (started, ended) != (1, 1) or min(charge, rollback, replays) < 0:
        raise ValueError("profile topology or counter")
    modules, blocks = {}, []
    for fields in lines[1:-1]:
        if fields[0] == "M" and len(fields) == 6:
            index = int(fields[1])
            if index in modules or not 0 <= index < 128:
                raise ValueError("duplicate or invalid module")
            build_id, start, end, name = fields[2:]
            if not re.fullmatch(r"(?:[0-9a-f]{2}){1,64}", build_id):
                raise ValueError("module build id unavailable")
            start, end = int(start, 16), int(end, 16)
            if not 0 < start < end < 2**64:
                raise ValueError("invalid module range")
            modules[index] = dict(build_id=build_id, start=start, end=end, name=name)
        elif fields[0] == "B" and len(fields) == 5:
            module, offset, instructions, hits = int(fields[1]), int(fields[2], 16), int(fields[3]), int(fields[4])
            if not 0 < instructions <= 2**32-1 or not 0 < hits <= 2**64-1:
                raise ValueError("invalid block counter")
            blocks.append((module, offset, instructions, hits))
        else:
            raise ValueError("unknown profile record")
    if len(blocks) != emitted or set(modules) != set(range(len(modules))):
        raise ValueError("profile coverage mismatch")
    normalized, observed = defaultdict(int), set()
    for module, offset, instructions, hits in blocks:
        key = (module, offset, instructions)
        if key in observed or module not in modules:
            raise ValueError("duplicate or disconnected block")
        observed.add(key)
        identity = modules[module]
        if not 0 <= offset < identity["end"]-identity["start"]:
            raise ValueError("block outside module")
        normalized[(identity["build_id"], offset, instructions)] += hits
    weighted = sum(instructions*hits for _, _, instructions, hits in blocks)
    if weighted != charge or charge-rollback != measured_instructions:
        raise ValueError("histogram and measured counter disagree")
    if replays or rollback:
        raise ValueError("replay attribution is outside this diagnostic")
    return {"modules": modules, "blocks": normalized, "entries": emitted,
            "weighted_charge": weighted, "rollback": rollback, "replays": replays,
            "module_unloads": unloads}


def compare_profiles(profiles):
    if len(profiles) != 3:
        raise ValueError("three complete profiles are required; no passing subset")
    keys = sorted(set().union(*(row["blocks"] for row in profiles)))
    names = {item["build_id"]: item["name"] for profile in profiles
             for item in profile["modules"].values()}
    differences = []
    for build_id, offset, instructions in keys:
        counts = [row["blocks"].get((build_id, offset, instructions), 0) for row in profiles]
        if len(set(counts)) > 1:
            differences.append({"build_id": build_id, "module": names[build_id],
                                "offset": hex(offset), "instructions_per_entry": instructions,
                                "hits": counts, "charged_instructions": [n*instructions for n in counts]})
    return {"samples": len(profiles), "totals": [row["weighted_charge"] for row in profiles],
            "varying_blocks": differences}


def observe(args, directory, target, expected, metered, expect_unload=False):
    directory.mkdir(mode=0o700)
    command = target
    if metered:
        (directory / "records").mkdir(mode=0o700)
        command = [str(args.sdk / "bin64/drrun"), "-quiet", "-follow_children",
                   "-disable_traces", "-vm_size", "32M", "-vmheap_size", "32M",
                   "-stderr_mask", "12", "-c", str(args.client), "-outdir",
                   str(directory / "records"), "--", *target]
    row = run_process(command, directory, WALL_MS, args.prlimit,
                      cpu_seconds=90, python_hash_seed="1")
    row.update(command=command, directory=str(directory))
    row["functional_ok"] = row["exit_code"] == 0 and row.get("stdout") == expected
    if not row["functional_ok"] or row.get("stderr") != "":
        row["errors"].append("fixed exit/output oracle failed")
    profile = None
    if metered:
        try:
            measurement = collect_records(args, directory, row["root_pid"])
            row["measurement"] = measurement
            errors = inspect_accounting(measurement, directory / "records", row["root_pid"], 1, False)[0]
            if (directory / "collector-stderr.txt").stat().st_size:
                errors.append("unexpected collector diagnostic")
            row["accounting_ok"] = not errors
            row["errors"].extend(errors)
            raw = bounded_bytes(directory / "count-locations.tsv", 1048576)
            row.update(profile_sha256=hashlib.sha256(raw).hexdigest(), profile_bytes=len(raw))
            profile = parse_profile(raw, measurement["instructions"])
            row["profile"] = {key: value for key, value in profile.items() if key != "blocks"}
        except ModuleUnloaded as error:
            row["profile_rejection"] = {"reason": "module_unload", "count": error.count}
            if not expect_unload:
                row["errors"].append("profile unavailable: " + str(error))
        except (OSError, ValueError, KeyError, TypeError) as error:
            row["errors"].append("profile unavailable: " + str(error))
    if expect_unload:
        row["expected_profile_rejection"] = "module_unload"
        if row.get("profile_rejection", {}).get("reason") != "module_unload":
            row["errors"].append("module-unload oracle was not rejected")
    row["passed"] = row["infrastructure_ok"] and not row["errors"]
    return row, profile


def input_paths(args):
    names = ("client/durante.c", "client/cpu_policy.h", "client/durante_runtime.h",
             "tests/count_locations.py", "tests/count_locations/profile.h",
             "tests/count_locations/oracle.S", "tests/count_locations/CMakeLists.txt",
             "tests/count_locations/unload_oracle.c", "tests/count_locations/unload_module.c",
             "tests/runtime_compat.py", "tests/runtime_repeatability.py",
             "tests/runtime_workloads.py", "tests/concurrent_spawn.py", "tests/fixtures.py", "collect.py")
    paths = {"source:" + name: ROOT / name for name in names}
    paths.update({"binary:runtime": Path(args.runtime_bin), "binary:client": args.client,
                  "binary:oracle": args.oracle, "binary:runner": args.runner_bin,
                  "binary:unload-oracle": args.unload_oracle,
                  "binary:unload-module": args.unload_module,
                  "binary:drrun": args.sdk / "bin64/drrun",
                  "binary:core": args.sdk / "lib64/release/libdynamorio.so",
                  "binary:drmgr": args.sdk / "ext/lib64/release/libdrmgr.so",
                  "manifest:runtime": args.sdk / ".durante-runtime.json",
                  "binary:prlimit": Path(args.prlimit), "binary:readelf": Path(args.readelf),
                  "binary:harness-python": Path(sys.executable).resolve()})
    paths.update({"operator:runtime-file:" + str(index): path
                  for index, path in enumerate(args.runtime_file)})
    return paths


def collect(args):
    args.output_dir.mkdir(parents=True, mode=0o700, exist_ok=False)
    plan = [{"workload": workload, "repeat": repeat, "state": "unrun", "errors": []}
            for repeat in range(1, 4) for workload in args.workload]
    collection = {"schema": 1, "authority": "diagnostic_only", "qualification_claim": False,
                  "rows": plan, "setup": [], "failures": [],
                  "limits": {"row_wall_ms": WALL_MS, "row_cpu_seconds": 90,
                             "campaign_seconds": CAMPAIGN_SECONDS, "histogram_bytes": 1048576},
                  "profile": {"runtime": "cpython", "runtime_executable": args.runtime_bin,
                              "cpython_hash_profile": "seed-1", "cpu": args.cpu,
                              "flags": list(CPYTHON_CONTROL_FLAGS), "environment": FIXED_ENV}}
    publish(args.output_dir / "plan.json", collection)
    provenance = InputProvenance(input_paths(args))
    profiles, failures = defaultdict(list), collection["failures"]
    original_affinity = os.sched_getaffinity(0)
    libc = previous_subreaper = None
    cleaned = restored = False
    before = after = None
    started = time.monotonic()
    deadline = started + CAMPAIGN_SECONDS
    try:
        provenance.capture("before")
        if provenance.errors:
            raise ValueError("named input identity unavailable")
        os.sched_setaffinity(0, {args.cpu})
        before = host_profile({args.cpu})
        collection["profile"]["host"] = before
        if args.workload and not before["process_personality"]["addr_no_randomize"]:
            raise ValueError("CPython location diagnostics require an inherited setarch -R control")
        libc, previous_subreaper = own_subreaper()
        with fixed_environment(FIXED_ENV):
            for metered in (False, True):
                if deadline - time.monotonic() < ROW_ALLOWANCE:
                    raise ValueError("oracle full-row allowance unavailable")
                row, profile = observe(args, args.output_dir / ("oracle-profile" if metered else "oracle-native"),
                                       [str(args.oracle)], "", metered)
                collection["setup"].append(row)
                if not row["passed"]:
                    raise ValueError("independent oracle execution failed")
                if metered:
                    if row["measurement"]["instructions"] != 26 or len(profile["modules"]) != 1:
                        raise ValueError("independent 26-instruction oracle failed")
                    notes = args.output_dir / "oracle-build-id"
                    notes.mkdir(mode=0o700)
                    identity = run_process([args.readelf, "-n", str(args.oracle)], notes, 10000,
                                           args.prlimit, cpu_seconds=5)
                    collection["setup"].append(identity)
                    expected_ids = re.findall(r"Build ID: ([0-9a-f]+)", identity.get("stdout", ""))
                    if (not identity["infrastructure_ok"] or identity["exit_code"] != 0
                            or identity.get("stderr") or expected_ids != [next(iter(profile["modules"].values()))["build_id"]]):
                        raise ValueError("loaded ELF identity differs from the independent file inspection")
            for metered in (False, True):
                if deadline - time.monotonic() < ROW_ALLOWANCE:
                    raise ValueError("module-unload oracle full-row allowance unavailable")
                row, _ = observe(args, args.output_dir / ("unload-profile" if metered else "unload-native"),
                                 [str(args.unload_oracle), str(args.unload_module)], "", metered,
                                 expect_unload=metered)
                collection["setup"].append(row)
                if not row["passed"]:
                    raise ValueError("module-unload rejection oracle failed")
            if args.workload:
                if deadline - time.monotonic() < ROW_ALLOWANCE:
                    raise ValueError("native startup-probe allowance unavailable")
                args.python_hash_seed = "1"
                collect_cpython_probe(args, collection, provenance)
            for row in plan:
                if deadline - time.monotonic() < ROW_ALLOWANCE:
                    row["errors"].append("full row and publication/cleanup allowance unavailable")
                    continue
                source, expected = PYTHON_STARTUP_CONTROLS[row["workload"]]
                target = [args.runtime_bin, *CPYTHON_CONTROL_FLAGS, "-c", source]
                directory = args.output_dir / ("repeat-%d-%s" % (row["repeat"], row["workload"]))
                observation, profile = observe(args, directory, target, expected, True)
                row.update(observation, state="completed")
                if row["passed"]:
                    profiles[row["workload"]].append(profile)
                if not row["cleanup_complete"]:
                    raise ValueError("row cleanup incomplete; later rows remain unrun")
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as error:
        failures.append(type(error).__name__ + ": " + str(error))
    finally:
        try:
            cleaned = cleanup_orphans()
        except OSError as error:
            failures.append("cleanup observation: " + str(error))
        try:
            restored = libc is None or libc.prctl(36, previous_subreaper, 0, 0, 0) == 0
            after = host_profile({args.cpu})
        except (OSError, ValueError) as error:
            failures.append("final host observation: " + str(error))
        finally:
            os.sched_setaffinity(0, original_affinity)
        provenance.capture("after")
    if not all(row.get("passed") for row in plan):
        failures.append("incomplete or failed fixed observation plan")
    if not cleaned or not restored or os.sched_getaffinity(0) != original_affinity:
        failures.append("cleanup or ownership restoration incomplete")
    if before != after or not provenance.unchanged():
        failures.append("host profile or named inputs changed")
    collection.update(passed=not failures, elapsed_seconds=round(time.monotonic()-started, 4),
                      comparison={} if failures else {name: compare_profiles(profiles[name]) for name in args.workload},
                      cleanup_complete=cleaned, subreaper_restored=restored,
                      affinity_restored=os.sched_getaffinity(0) == original_affinity,
                      host_after=after, inputs_unchanged=provenance.unchanged(),
                      input_provenance={"files": provenance.files, "errors": provenance.errors})
    publish(args.output_dir / "results.json", collection)
    print(json.dumps({key: collection[key] for key in ("passed", "qualification_claim", "failures", "cleanup_complete", "inputs_unchanged")}))
    return int(bool(failures))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("sdk", "client", "runner-bin", "oracle", "unload-oracle", "unload-module", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--runtime-bin", default=sys.executable)
    parser.add_argument("--runtime-file", type=Path, action="append", default=[])
    parser.add_argument("--workload", choices=PYTHON_STARTUP_CONTROLS, action="append")
    parser.add_argument("--oracle-only", action="store_true")
    parser.add_argument("--cpu", type=bounded_number(0, 1048575))
    args = parser.parse_args(argv)
    if args.oracle_only and args.workload:
        parser.error("--oracle-only cannot discard requested CPython workloads")
    args.workload = [] if args.oracle_only else list(dict.fromkeys(args.workload or PYTHON_STARTUP_CONTROLS))
    for name in ("sdk", "client", "runner_bin", "oracle", "unload_oracle", "unload_module", "output_dir"):
        setattr(args, name, getattr(args, name).absolute())
    args.prlimit, args.readelf = executable_path("prlimit"), executable_path("readelf")
    args.runtime_bin = executable_path(args.runtime_bin)
    if not args.prlimit or not args.readelf or not args.runtime_bin:
        parser.error("prlimit, readelf and the selected CPython executable are required")
    allowed = os.sched_getaffinity(0)
    args.cpu = min(allowed) if args.cpu is None else args.cpu
    if args.cpu not in allowed:
        parser.error("selected CPU is outside the allowed affinity")
    return collect(args)


if __name__ == "__main__":
    raise SystemExit(main())
