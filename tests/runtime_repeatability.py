#!/usr/bin/env python3
"""Record fresh-process runtime observations and qualify their raw-count spread."""
import argparse
from contextlib import contextmanager
from dataclasses import asdict, replace
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import time
from types import SimpleNamespace

from concurrent_spawn import bounded_number, cleanup_orphans, own_subreaper
from runtime_compat import InputProvenance, bounded_bytes, executable_path, file_identity
import runtime_workloads as workloads

ROOT = Path(__file__).resolve().parents[1]
RUNTIMES = (*workloads.RUNTIMES, "pypy")
COUNTERS = ("instructions", "rep_bytes", "rep_entries", "unsupported_rep_entries", "signals_observed")
KINDS = ("movs_bytes", "stos_bytes", "movs_entries", "stos_entries")
METERED = ("client-rep-off", "supervisor")
REQUIRED_INPUTS = {"binary:runtime", "binary:client", "binary:runner", "binary:drrun",
                   "binary:core", "manifest:runtime", "binary:prlimit", "binary:harness-python",
                   "source:tests/concurrent_spawn.py", "source:tests/fixtures.py"}
FIXED_ENV = {"PATH": os.defpath, "LANG": "C", "LC_ALL": "C", "TZ": "UTC", "MALLOC_ARENA_MAX": "2"}
CPYTHON_HASH_PROFILES = {"isolated": None, "random": "random", "seed-1": "1"}
CPYTHON_CONTROL_FLAGS = ("-P", "-s", "-S")
PYPY_HASH_PROFILES = dict(CPYTHON_HASH_PROFILES)
PYPY_CONTROL_FLAGS = ("-s", "-S")
PYTHON_STARTUP_CONTROLS = {
    "startup": ("print('startup:ok')\n", "startup:ok\n"),
    "imports": ("import gc\nfrom pathlib import Path\nimport subprocess\nimport sys\n"
                "import threading\nprint('imports:ok')\n", "imports:ok\n"),
}
CPYTHON_HASH_WORKLOADS = ("allocation-gc", *PYTHON_STARTUP_CONTROLS)
PYPY_HASH_WORKLOADS = CPYTHON_HASH_WORKLOADS
ADDR_NO_RANDOMIZE = 0x40000
CPYTHON_PROBE_LEGACY = """import gc, json, os, pathlib, subprocess, sys, threading
paths = sorted({str(pathlib.Path(value).resolve())
                for module in list(sys.modules.values())
                for attribute in ("__file__", "__cached__")
                if isinstance(value := getattr(module, attribute, None), str)
                and pathlib.Path(value).is_file()})
print(json.dumps({"executable": str(pathlib.Path(sys.executable).resolve()),
                  "flags": {name: getattr(sys.flags, name) for name in
                            ("isolated", "ignore_environment", "safe_path", "no_user_site", "no_site", "hash_randomization")},
                  "python_environment": {key: value for key, value in os.environ.items() if key.startswith("PYTHON")},
                  "hash_sentinel": hash("durante-repeatability-v1"),
                  "hash_algorithm": sys.hash_info.algorithm,
                  "stdlib_paths": paths}, sort_keys=True))
"""
CPYTHON_PROBE = CPYTHON_PROBE_LEGACY.replace(
    'print(json.dumps({',
    'personality = int(pathlib.Path("/proc/self/personality").read_text().strip(), 16)\n'
    'print(json.dumps({"process_personality": {"value": personality, '
    '"addr_no_randomize": bool(personality & 0x40000)}, ', 1)
CPYTHON_PROBES = {1: CPYTHON_PROBE_LEGACY, 2: CPYTHON_PROBE}
# PyPy 7.3.19 has neither -P nor sys.flags.safe_path. Preserve the historical
# CPython probe bytes while recording the different PyPy path/flag contract.
PYPY_PROBE = CPYTHON_PROBE.replace('"safe_path", ', '').replace(
    '"executable":', '"implementation": sys.implementation.name, '
    '"working_directory": os.getcwd(), "sys_path": sys.path, "executable":', 1)
MAX_JSON = 32 * 1024 * 1024
LIMITATIONS = [
    "fresh processes including startup; no persistent-runtime warmup or steady-state claim",
    "recorded workload, installation, CPU/ISA, options and environment only; not general language qualification",
    "runtime JIT remains enabled by default; compilation/deoptimization and scheduling are not controlled",
    "named input hashes and optional operator image digest do not attest every loaded library or the host",
    "application-mutable diagnostic counts, not CPU time, time coefficients or trusted judgement",
    "run-profile observations do not qualify isolate; existing isolate evidence remains a separate prerequisite",
    "explicit CPython hash controls change startup flags and do not qualify the default isolated profile",
    "explicit PyPy hash controls use -s -S, retain the script/current-directory import path and do not qualify -I -S; hash_randomization alone does not establish the effective seed",
    "Python startup/import controls include loader, output and shutdown work; they are not region counts or counter correctness proofs",
    "recorded ADDR_NO_RANDOMIZE is inherited process state, not proof that all runtime allocation addresses are deterministic; legacy state is unknown",
]


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def publish(path, value):
    data = json.dumps(value, indent=2, sort_keys=True, allow_nan=False).encode() + b"\n"
    if len(data) > MAX_JSON:
        raise ValueError("result exceeds the 32-MiB publication bound")
    temporary = path.with_name(path.name + ".pending")
    with temporary.open("wb") as stream:
        os.chmod(temporary, 0o600)
        stream.write(data)
    temporary.replace(path)


def read_collection(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate collection field: " + key)
            result[key] = value
        return result

    return json.loads(bounded_bytes(path, MAX_JSON), object_pairs_hook=unique,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError("nonfinite JSON " + value)))


def integer(value, low, high, name):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name} must be an integer in {low}..{high}")
    return value


def fraction_json(value):
    return {"numerator": value.numerator, "denominator": value.denominator}


def count_spread(values, maximum_ppm):
    """Exact median/range; zero median with nonzero range cannot pass a ratio gate."""
    if not values:
        raise ValueError("empty counter sample")
    for value in values:
        integer(value, 0, (1 << 64) - 1, "counter")
    integer(maximum_ppm, 0, 1_000_000, "maximum spread ppm")
    ordered = sorted(values)
    middle = len(ordered) // 2
    median = Fraction(ordered[middle]) if len(ordered) % 2 else Fraction(ordered[middle - 1] + ordered[middle], 2)
    width = ordered[-1] - ordered[0]
    spread = Fraction(width * 1_000_000, 1) / median if median else (Fraction(0) if width == 0 else None)
    passed = width * median.denominator * 1_000_000 <= maximum_ppm * median.numerator
    return {"samples": len(values), "minimum": ordered[0], "maximum": ordered[-1],
            "range": width, "median": fraction_json(median),
            "spread_ppm": fraction_json(spread) if spread is not None else None, "passed": passed}


def breakdown_values(row):
    part = row.get("rep_breakdown")
    if not isinstance(part, dict) or set(part) != {"version", *KINDS} or type(part.get("version")) is not int or part["version"] != 1:
        raise ValueError("MOVS/STOS breakdown version 1 is required; legacy absence is not zero")
    values = {name: integer(part[name], 0, (1 << 64) - 1, name) for name in KINDS}
    if (values["movs_bytes"] + values["stos_bytes"] != row["rep_bytes"]
            or values["movs_entries"] + values["stos_entries"] != row["rep_entries"]
            or any(values[kind + "_bytes"] and not values[kind + "_entries"] for kind in ("movs", "stos"))):
        raise ValueError("MOVS/STOS partition contradicts the REP aggregate")
    return values


def measurement_values(measurement, case, rep):
    """Validate stored integer partitions independently of a row's passed flag."""
    if (not isinstance(measurement, dict) or type(measurement.get("schema")) is not int or measurement["schema"] != 1
            or measurement.get("metric") != "bb_entry_app_instructions" or measurement.get("scope") != "process_tree_user_code"
            or measurement.get("valid") is not True
            or measurement.get("complete") is not True or measurement.get("authority") != "diagnostic_only"
            or measurement.get("integrity_verified") is not False
            or not workloads.same_json(measurement.get("measurement_contract"), workloads.collector.measurement_contract())
            or measurement.get("rep_bytes_enabled") is not rep):
        raise ValueError("complete diagnostic measurement is unavailable")
    processes, segments = measurement.get("processes"), measurement.get("segments")
    if (not isinstance(processes, list) or not isinstance(segments, list)
            or type(measurement.get("process_count")) is not int or measurement["process_count"] != case.processes
            or type(measurement.get("image_count")) is not int or measurement["image_count"] != case.images
            or len(processes) != case.processes or len(segments) != case.images):
        raise ValueError("recorded workload topology changed")
    if workloads.topology_errors(measurement, measurement.get("root_pid"), case):
        raise ValueError("recorded workload lineage or thread topology changed")
    values = None
    for row in [measurement, *processes, *segments]:
        if not isinstance(row, dict):
            raise ValueError("invalid process/image observation")
        current = {name: integer(row.get(name), 0, (1 << 64) - 1, name) for name in COUNTERS}
        if current["instructions"] == 0 or (current["rep_bytes"] and not current["rep_entries"]):
            raise ValueError("invalid instruction or REP demand")
        current.update(breakdown_values(row))
        if not rep and any(current[name] for name in (*COUNTERS[1:4], *KINDS)):
            raise ValueError("REP-disabled observation contains REP work")
        if values is None:
            values = current
    for name in (*COUNTERS, *KINDS):
        for group in (processes, segments):
            total = sum(row[name] if name in COUNTERS else row["rep_breakdown"][name] for row in group)
            if total != values[name]:
                raise ValueError("tree/process/image counter sums disagree: " + name)
    expected_quality = "signals_observed" if values["signals_observed"] else "uninterrupted"
    expected_rep = "disabled" if not rep else ("partial" if values["unsupported_rep_entries"] else "movs_stos_only")
    if measurement.get("quality") != expected_quality or measurement.get("rep_telemetry_status") != expected_rep:
        raise ValueError("counter quality/coverage label contradicts the observation")
    return values



def hash_profile(profile):
    selected = "isolated"
    for runtime, profiles in (("cpython", CPYTHON_HASH_PROFILES), ("pypy", PYPY_HASH_PROFILES)):
        name = profile.get(runtime + "_hash_profile", "isolated")
        if name not in profiles or (name != "isolated" and profile.get("runtime") != runtime):
            raise ValueError("unknown or incompatible " + runtime + " hash profile")
        if name != "isolated":
            selected = name
    return selected


def controlled_cases(cases, name, runtime="cpython"):
    if name == "isolated":
        return cases
    # Child-process fixtures deliberately retain their own -I flags. Hash
    # controls apply only to these explicitly selected single-process cases.
    if runtime not in ("cpython", "pypy") or not cases or any(case.name not in {runtime + "-" + workload for workload in CPYTHON_HASH_WORKLOADS}
                        for case in cases):
        raise ValueError("explicit " + runtime + " hash controls require allocation-gc, startup or imports")
    if any(case.command[1:3] != ["-I", "-S"] for case in cases):
        raise ValueError("default Python startup contract changed")
    flags = CPYTHON_CONTROL_FLAGS if runtime == "cpython" else PYPY_CONTROL_FLAGS
    return [replace(case, command=[case.command[0], *flags, *case.command[3:]]) for case in cases]


def python_startup_cases(runtime, executable):
    # These opt-in controls belong only to this repeatability tool. Do not add
    # them to the ordinary language compatibility matrix or its default plan.
    if runtime not in ("cpython", "pypy"):
        raise ValueError("startup and imports controls require cpython or pypy")
    return [workloads.Workload(runtime + "-" + name, runtime, [executable, "-I", "-S", "-c", source],
                               expected, 1, 1)
            for name, (source, expected) in PYTHON_STARTUP_CONTROLS.items()]


def profile_environment(profile):
    environment = dict(FIXED_ENV)
    seed = CPYTHON_HASH_PROFILES[hash_profile(profile)]
    if seed is not None:
        environment["PYTHONHASHSEED"] = seed
    return environment


def probe_errors(collection, profile, files):
    name = hash_profile(profile)
    for runtime in ("cpython", "pypy"):
        if name == "isolated" or runtime != profile.get("runtime"):
            if (collection.get(runtime + "_probe") is not None or profile.get(runtime + "_effective") is not None
                    or profile.get(runtime + "_probe_version") is not None):
                return ["isolated or foreign profile contains an unsolicited hash-control probe"]
    if name == "isolated":
        return []
    if profile["runtime"] == "pypy":
        return pypy_probe_errors(collection, profile, files)
    probe = collection.get("cpython_probe") or {}
    effective = profile.get("cpython_effective")
    try:
        observed = json.loads(probe.get("stdout", ""))
        version = profile.get("cpython_probe_version", 1)
        if type(version) is not int or version not in CPYTHON_PROBES:
            raise ValueError("unknown CPython startup probe version")
        if version == 2:
            personality = observed.get("process_personality")
            if (not valid_personality(personality)
                    or not workloads.same_json(personality, profile.get("host", {}).get("process_personality"))):
                raise ValueError("native CPython and harness process personality differ or are unavailable")
        elif "process_personality" in observed or "process_personality" in profile.get("host", {}):
            raise ValueError("legacy CPython probe cannot establish process personality")
        expected_flags = {"isolated": 0, "ignore_environment": 0, "safe_path": True,
                          "no_user_site": 1, "no_site": 1, "hash_randomization": 1}
        seed = CPYTHON_HASH_PROFILES[name]
        if (probe.get("infrastructure_ok") is not True or probe.get("cleanup_complete") is not True
                or type(probe.get("exit_code")) is not int or probe["exit_code"] != 0
                or probe.get("timeout") is not False or probe.get("stderr") != "" or probe.get("errors")
                or probe.get("python_hash_seed") != seed
                or probe.get("command") != [profile["runtime_executable"], *CPYTHON_CONTROL_FLAGS, "-c", CPYTHON_PROBES[version]]
                or not workloads.same_json(observed, effective)
                or not workloads.same_json(observed.get("flags"), expected_flags)
                or observed.get("python_environment") != {"PYTHONHASHSEED": seed}
                or observed.get("executable") != files["binary:runtime"]["before"]["resolved_path"]
                or type(observed.get("hash_sentinel")) is not int
                or not -(1 << 63) <= observed["hash_sentinel"] < (1 << 63)
                or not isinstance(observed.get("hash_algorithm"), str) or not observed["hash_algorithm"]):
            raise ValueError("effective CPython flags, hash input or runtime identity differs")
        paths = observed.get("stdlib_paths")
        if (not isinstance(paths, list) or not 1 <= len(paths) <= 256
                or any(not isinstance(path, str) or not Path(path).is_absolute() for path in paths)
                or paths != sorted(set(paths))):
            raise ValueError("CPython probe module paths are unavailable or invalid")
        for index, path in enumerate(paths):
            if files["probe:cpython-module:" + str(index)]["path"] != path:
                raise ValueError("CPython module provenance differs from its probe")
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        return ["CPython startup probe unavailable or incompatible: " + str(error)]
    return []


def collect_cpython_probe(args, collection, provenance):
    profile = collection["profile"]
    if profile["runtime"] != "cpython" or hash_profile(profile) == "isolated":
        return
    profile["cpython_probe_version"] = 2
    directory = args.output_dir / "cpython-startup-probe"
    directory.mkdir(mode=0o700)
    command = [profile["runtime_executable"], *CPYTHON_CONTROL_FLAGS, "-c", CPYTHON_PROBE]
    row = workloads.run_process(command, directory, 10000, args.prlimit,
                                python_hash_seed=args.python_hash_seed)
    row.update(command=command, directory=str(directory))
    collection["cpython_probe"] = row
    effective = json.loads(row.get("stdout", ""))
    profile["cpython_effective"] = effective
    paths = effective.get("stdlib_paths", [])
    if not isinstance(paths, list) or not 1 <= len(paths) <= 256:
        raise ValueError("CPython probe module inventory unavailable")
    for index, path in enumerate(paths):
        provenance.files["probe:cpython-module:" + str(index)] = {
            "path": path, "before": file_identity(path), "after": None, "unchanged": False}
    errors = probe_errors(collection, profile, provenance.files)
    if errors:
        raise ValueError("; ".join(errors))


def pypy_probe_errors(collection, profile, files):
    probe = collection.get("pypy_probe") or {}
    try:
        observed = json.loads(probe.get("stdout", ""))
        version = profile.get("pypy_probe_version")
        if type(version) is not int or version != 1:
            raise ValueError("unknown PyPy startup probe version")
        personality = observed.get("process_personality")
        if (not valid_personality(personality)
                or not workloads.same_json(personality, profile.get("host", {}).get("process_personality"))):
            raise ValueError("native PyPy and harness process personality differ or are unavailable")
        seed = PYPY_HASH_PROFILES[hash_profile(profile)]
        flags = observed.get("flags")
        # This flag remains 0 on PyPy 7.3.19 even with an explicit seed. The
        # forwarded input and observed sentinel are recorded independently.
        if (not isinstance(flags, dict) or type(flags.get("hash_randomization")) is not int
                or flags["hash_randomization"] not in (0, 1)
                or not workloads.same_json(flags, {"isolated": 0, "ignore_environment": 0,
                    "no_user_site": 1, "no_site": 1, "hash_randomization": flags["hash_randomization"]})):
            raise ValueError("effective PyPy startup flags differ")
        if (probe.get("infrastructure_ok") is not True or probe.get("cleanup_complete") is not True
                or type(probe.get("exit_code")) is not int or probe["exit_code"] != 0
                or probe.get("timeout") is not False or probe.get("stderr") != "" or probe.get("errors")
                or probe.get("python_hash_seed") != seed
                or probe.get("command") != [profile["runtime_executable"], *PYPY_CONTROL_FLAGS, "-c", PYPY_PROBE]
                or not workloads.same_json(observed, profile.get("pypy_effective"))
                or observed.get("implementation") != "pypy"
                or observed.get("python_environment") != {"PYTHONHASHSEED": seed}
                or observed.get("executable") != files["binary:runtime"]["before"]["resolved_path"]
                or type(observed.get("hash_sentinel")) is not int
                or not -(1 << 63) <= observed["hash_sentinel"] < (1 << 63)
                or not isinstance(observed.get("hash_algorithm"), str) or not observed["hash_algorithm"]):
            raise ValueError("effective PyPy hash input or runtime identity differs")
        directory, search = observed.get("working_directory"), observed.get("sys_path")
        if (not isinstance(directory, str) or not Path(directory).is_absolute()
                or directory != probe.get("directory")
                or not isinstance(search, list) or not 2 <= len(search) <= 256 or search[0] != ""
                or any(not isinstance(path, str) or not Path(path).is_absolute() for path in search[1:])):
            raise ValueError("PyPy probe working directory or import path is unavailable or incompatible")
        paths = observed.get("stdlib_paths")
        if (not isinstance(paths, list) or not 1 <= len(paths) <= 256
                or any(not isinstance(path, str) or not Path(path).is_absolute() for path in paths)
                or paths != sorted(set(paths))):
            raise ValueError("PyPy probe module paths are unavailable or invalid")
        for index, path in enumerate(paths):
            if files["probe:pypy-module:" + str(index)]["path"] != path:
                raise ValueError("PyPy module provenance differs from its probe")
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        return ["PyPy startup probe unavailable or incompatible: " + str(error)]
    return []


def collect_pypy_probe(args, collection, provenance):
    profile = collection["profile"]
    if profile["runtime"] != "pypy" or hash_profile(profile) == "isolated":
        return
    profile["pypy_probe_version"] = 1
    directory = args.output_dir / "pypy-startup-probe"
    directory.mkdir(mode=0o700)
    command = [profile["runtime_executable"], *PYPY_CONTROL_FLAGS, "-c", PYPY_PROBE]
    row = workloads.run_process(command, directory, 10000, args.prlimit,
                                python_hash_seed=args.python_hash_seed)
    row.update(command=command, directory=str(directory))
    collection["pypy_probe"] = row
    effective = json.loads(row.get("stdout", ""))
    profile["pypy_effective"] = effective
    paths = effective.get("stdlib_paths", [])
    if not isinstance(paths, list) or not 1 <= len(paths) <= 256:
        raise ValueError("PyPy probe module inventory unavailable")
    for index, path in enumerate(paths):
        provenance.files["probe:pypy-module:" + str(index)] = {
            "path": path, "before": file_identity(path), "after": None, "unchanged": False}
    errors = probe_errors(collection, profile, provenance.files)
    if errors:
        raise ValueError("; ".join(errors))


def profile_cases(profile, files):
    runtime = profile["runtime"]
    if runtime not in RUNTIMES or profile.get("execution_profile") != "run":
        raise ValueError("unknown runtime or execution profile")
    selected = profile["workload_names"]
    if not isinstance(selected, list) or not selected or len(selected) != len(set(selected)):
        raise ValueError("empty or duplicate workload selection")
    available = workloads.workload_cases({runtime: profile["runtime_executable"]}, Path(profile["classes_directory"]))
    if runtime in ("cpython", "pypy", "node"):
        fixture = "workload.js" if runtime == "node" else "workload.py"
        source = files.get("source:tests/runtime_workload_fixtures/" + fixture)
        path = source.get("path") if isinstance(source, dict) else None
        if not isinstance(path, str) or not Path(path).is_absolute():
            raise ValueError("recorded fixture source path is unavailable or invalid")
        # Evaluation may use another checkout. Bind only the fixed fixture slot
        # to its recorded invocation path; all other command/oracle fields stay
        # independently reconstructed, and provenance is checked below.
        fixture_index = 1 + len(workloads.NODE_FLAGS) if runtime == "node" else 3
        for index, case in enumerate(available):
            command = list(case.command)
            if len(command) <= fixture_index or command[fixture_index] != str(workloads.FIXTURES / fixture):
                raise ValueError("fixed runtime fixture command layout changed")
            command[fixture_index] = path
            available[index] = replace(case, command=command)
    if runtime in ("cpython", "pypy"):
        available += python_startup_cases(runtime, profile["runtime_executable"])
    cases = controlled_cases([case for case in available if case.name in selected], hash_profile(profile), runtime)
    if selected != [case.name for case in cases] or not workloads.same_json(profile.get("workloads"), [asdict(case) for case in cases]):
        raise ValueError("recorded commands or workload oracle do not match the fixed profile")
    return cases


def profile_errors(profile):
    errors = []
    if profile.get("environment") != profile_environment(profile) or profile.get("fresh_processes") is not True:
        errors.append("execution environment or process-lifetime policy changed")
    integer(profile.get("timeout_ms"), 1000, 180000, "profile wall limit")
    image = profile.get("runtime_image")
    if image is not None and (not isinstance(image, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", image)):
        errors.append("invalid operator runtime image digest")
    version = profile.get("versions", {}).get(profile["runtime"], {})
    if version.get("path") != profile["runtime_executable"] or not isinstance(version.get("version"), str) or not version["version"]:
        errors.append("selected runtime version is unavailable")
    host = profile.get("host") or {}
    affinity, cpus = host.get("affinity"), host.get("cpus")
    if (host.get("system") != "Linux" or host.get("machine") != "x86_64" or not host.get("kernel")
            or not isinstance(affinity, list) or len(affinity) != 1 or type(affinity[0]) is not int
            or not isinstance(cpus, list) or len(cpus) != 1
            or cpus[0].get("processor") != str(affinity[0]) or not cpus[0].get("flags")):
        errors.append("fixed single-CPU ISA profile is unavailable")
    if "process_personality" in host and not valid_personality(host["process_personality"]):
        errors.append("recorded process personality is invalid or inconsistent")
    return errors


def expected_command(profile, case, mode, directory):
    # Independent check of run_case's actual invocation, including mode options.
    if mode == "native":
        return case.command
    if mode == "supervisor":
        return [profile["runner"], "run", "--drrun", profile["drrun"], "--client", profile["client"],
                "--report", str(directory / "report.json"), "--records-dir", str(directory / "records"),
                "--timeout-ms", str(profile["timeout_ms"]), "--", *case.command]
    prefix = [profile["drrun"], "-quiet", "-follow_children", "-disable_traces", "-vm_size", "32M",
              "-vmheap_size", "32M", "-stderr_mask", "12"]
    if mode == "client-rep-off":
        prefix += ["-c", profile["client"], "-outdir", str(directory / "records")]
    return [*prefix, "--", *case.command]


def fixed_plan(cases, repetitions):
    return [{"repeat": repeat, "workload": case.name, "mode": mode}
            for repeat in range(1, repetitions + 1) for case in cases for mode in workloads.MODES]


def evaluate(collection):
    result = {"schema": 1, "authority": "diagnostic_only", "integrity_verified": False,
              "collection_complete": False, "collection_passed": False, "qualification_passed": False,
              "failures": [], "groups": [], "limitations": LIMITATIONS}
    failures = result["failures"]
    try:
        result["collection_sha256"] = digest(collection)
        if type(collection.get("schema")) is not int or collection["schema"] != 1 or collection.get("authority") != "diagnostic_only":
            raise ValueError("unsupported collection schema/authority")
        options, profile = collection["options"], collection["profile"]
        repetitions = integer(options["repeats"], 3, 9, "repetitions")
        threshold = integer(options["max_spread_ppm"], 0, 1_000_000, "maximum spread ppm")
        provenance = collection["input_provenance"]
        files = provenance["files"]
        cases = profile_cases(profile, files)
        result.update(options=options, profile_sha256=collection.get("profile_sha256"))
        if collection.get("profile_sha256") != digest(profile):
            failures.append("profile digest differs from the recorded profile")
        failures.extend(profile_errors(profile))
        before, after = collection.get("host_before"), collection.get("host_after")
        if not before or before != after or profile.get("host") != before:
            failures.append("CPU affinity/ISA/host profile changed or is unavailable")
        failures.extend(probe_errors(collection, profile, files))
        required = REQUIRED_INPUTS | {"source:tests/runtime_repeatability.py", "source:tests/runtime_workloads.py",
                                    "source:tests/runtime_compat.py", "source:collect.py"}
        if profile["runtime"] == "java":
            required |= {"binary:javac", "binary:java:jspawnhelper", "binary:java:modules", "binary:java:server/libjvm.so", "generated:RuntimeWorkload.class"}
        fixture = {"java": "RuntimeWorkload.java", "node": "workload.js", "cpython": "workload.py", "pypy": "workload.py"}[profile["runtime"]]
        required.add("source:tests/runtime_workload_fixtures/" + fixture)
        if (not required <= files.keys() or provenance.get("errors") or collection.get("inputs_unchanged") is not True
                or any(not item.get("before") or item.get("before") != item.get("after") or item.get("unchanged") is not True for item in files.values())):
            failures.append("runtime/binary/fixture input identity changed or is unavailable")
        hashes = {name: item["before"]["sha256"] for name, item in files.items() if item.get("before")}
        if any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value) for value in hashes.values()):
            failures.append("invalid input SHA256")
        if profile.get("input_sha256") != hashes:
            failures.append("profile input hashes differ from collection provenance")
        plan = fixed_plan(cases, repetitions)
        rows = collection["rows"]
        if not workloads.same_json(collection.get("plan"), plan) or not workloads.same_json([{key: row.get(key) for key in ("repeat", "workload", "mode")} for row in rows], plan):
            raise ValueError("fixed observation matrix is missing, duplicated or reordered")
        result["collection_complete"] = all(row.get("state") == "complete" for row in rows)
        if collection.get("setup_complete") is not True or collection.get("cleanup_complete") is not True:
            failures.append("setup or descendant cleanup did not complete")
        failures.extend(collection.get("failures", []))
        samples = {(case.name, mode): [] for case in cases for mode in METERED}
        case_by_name = {case.name: case for case in cases}
        for row in rows:
            case, mode = case_by_name[row["workload"]], row["mode"]
            label = f"{case.name}/{mode}/repeat-{row['repeat']}"
            if (row.get("state") != "complete" or row.get("passed") is not True or row.get("errors")
                    or row.get("infrastructure_ok") is not True or row.get("cleanup_complete") is not True
                    or type(row.get("exit_code")) is not int or row["exit_code"] != 0
                    or row.get("timeout") is not False or row.get("stdout") != case.expected or row.get("stderr") != ""):
                failures.append(label + ": incomplete, failed or skipped observation")
                continue
            if row.get("python_hash_seed") != CPYTHON_HASH_PROFILES[hash_profile(profile)]:
                failures.append(label + ": forwarded Python hash input differs from the profile")
                continue
            if row.get("command") != expected_command(profile, case, mode, Path(row["directory"])):
                failures.append(label + ": execution command differs from the fixed profile")
                continue
            if mode in METERED:
                try:
                    if mode == "supervisor":
                        if workloads.envelope_errors(row.get("report", {})):
                            raise ValueError("supervisor execution/cleanup contract failed")
                        if not workloads.same_json(row["report"].get("measurement"), row.get("measurement")):
                            raise ValueError("supervisor and observation measurements differ")
                    values = measurement_values(row.get("measurement"), case, mode == "supervisor")
                    samples[(case.name, mode)].append(values)
                except (ValueError, KeyError, TypeError, AttributeError) as error:
                    failures.append(label + ": " + str(error))
        result["collection_passed"] = result["collection_complete"] and not failures
        for (name, mode), observations in samples.items():
            group = {"workload": name, "mode": mode, "expected_samples": repetitions,
                     "observed_samples": len(observations), "metrics": {}, "passed": False}
            # A failed member invalidates its whole group: never fit a successful subset.
            if len(observations) == repetitions:
                group["metrics"] = {metric: count_spread([row[metric] for row in observations], threshold)
                                    for metric in (*COUNTERS, *KINDS)}
                group["passed"] = all(metric["passed"] for metric in group["metrics"].values())
                for metric, summary in group["metrics"].items():
                    if not summary["passed"]:
                        failures.append(f"{name}/{mode}/{metric}: spread exceeds {threshold} ppm")
            else:
                failures.append(f"{name}/{mode}: full repeated sample unavailable; no subset statistics")
            result["groups"].append(group)
        result["qualification_passed"] = result["collection_passed"] and not failures
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        failures.append("invalid collection: " + str(error))
    return result


@contextmanager
def fixed_environment(environment=None):
    previous = dict(os.environ)
    os.environ.clear()
    os.environ.update(FIXED_ENV if environment is None else environment)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(previous)


def valid_personality(value):
    return (isinstance(value, dict) and value.keys() == {"value", "addr_no_randomize"}
            and type(value.get("value")) is int and 0 <= value["value"] <= (1 << 32) - 1
            and type(value.get("addr_no_randomize")) is bool
            and value["addr_no_randomize"] == bool(value["value"] & ADDR_NO_RANDOMIZE))


def host_profile(cpus):
    if sys.platform != "linux" or platform.machine() != "x86_64":
        raise ValueError("repeatability collection requires Linux x86-64")
    selected = []
    for block in bounded_bytes(Path("/proc/cpuinfo"), 4 * 1024 * 1024).decode().split("\n\n"):
        fields = dict(line.split(":", 1) for line in block.splitlines() if ":" in line)
        fields = {key.strip(): value.strip() for key, value in fields.items()}
        if fields.get("processor", "").isdigit() and int(fields["processor"]) in cpus:
            selected.append({key: fields.get(key) for key in ("processor", "vendor_id", "cpu family", "model", "model name", "stepping", "microcode", "flags")})
    if len(selected) != len(cpus) or any(not row["flags"] for row in selected):
        raise ValueError("selected CPU ISA profile is unavailable")
    personality = bounded_bytes(Path("/proc/self/personality"), 32).decode("ascii").strip()
    if not re.fullmatch(r"[0-9a-fA-F]{8}", personality):
        raise ValueError("process personality is unavailable")
    value = int(personality, 16)
    return {"system": platform.system(), "machine": platform.machine(), "kernel": platform.release(),
            "affinity": sorted(os.sched_getaffinity(0)), "cpus": selected,
            "process_personality": {"value": value, "addr_no_randomize": bool(value & ADDR_NO_RANDOMIZE)}}


def input_paths(args, executable):
    names = ("tests/runtime_repeatability.py", "tests/runtime_workloads.py", "tests/runtime_compat.py",
             "tests/concurrent_spawn.py", "tests/fixtures.py", "collect.py",
             "tests/runtime_workload_fixtures/RuntimeWorkload.java",
             "tests/runtime_workload_fixtures/workload.py", "tests/runtime_workload_fixtures/workload.js")
    inputs = {"source:" + name: ROOT / name for name in names}
    inputs.update({"binary:runtime": executable, "binary:client": args.client, "binary:runner": args.runner_bin,
                   "binary:drrun": args.drrun, "binary:core": args.sdk / "lib64/release/libdynamorio.so",
                   "manifest:runtime": args.sdk / ".durante-runtime.json", "binary:prlimit": args.prlimit,
                   "binary:harness-python": Path(sys.executable).resolve()})
    inputs.update({"operator:runtime-file:" + str(index): path for index, path in enumerate(args.runtime_file)})
    if args.runtime == "java":
        java_home = Path(executable).resolve().parent.parent if executable else None
        inputs["binary:javac"] = args.javac
        for name in ("jspawnhelper", "modules", "server/libjvm.so"):
            inputs["binary:java:" + name] = java_home / "lib" / name if java_home else None
    return inputs


def collect(args):
    selected_hash_profile = "isolated"
    if args.runtime not in ("cpython", "pypy") and set(args.workload or []) & PYTHON_STARTUP_CONTROLS.keys():
        raise ValueError("startup and imports controls require cpython or pypy")
    for runtime, profiles in (("cpython", CPYTHON_HASH_PROFILES), ("pypy", PYPY_HASH_PROFILES)):
        name = getattr(args, runtime + "_hash_profile", "isolated")
        if (name not in profiles or name != "isolated" and (args.runtime != runtime or not args.workload
                or not set(args.workload) <= set(CPYTHON_HASH_WORKLOADS))):
            raise ValueError("explicit " + runtime + " hash controls require " + runtime + " and allocation-gc, startup or imports")
        if name != "isolated":
            selected_hash_profile = name
    args.python_hash_seed = CPYTHON_HASH_PROFILES[selected_hash_profile]
    for name in ("sdk", "client", "runner_bin", "output_dir"):
        setattr(args, name, getattr(args, name).absolute())
    args.output_dir.mkdir(parents=True, mode=0o700, exist_ok=False)
    executable = executable_path(args.runtime_bin or {"java": "java", "cpython": "python3", "node": "node", "pypy": "pypy3"}[args.runtime])
    args.javac, args.prlimit = executable_path(args.javac), executable_path("prlimit")
    args.drrun = args.sdk / "bin64/drrun"
    classes = args.output_dir / "classes"
    cases = workloads.workload_cases({args.runtime: executable or str(Path(args.runtime_bin or args.runtime).absolute())}, classes)
    if args.runtime in ("cpython", "pypy") and args.workload:
        cases += python_startup_cases(args.runtime, cases[0].command[0])
    if args.workload:
        cases = [case for case in cases if any(case.name == args.runtime + "-" + name or case.name.startswith(args.runtime + "-" + name + "-") for name in args.workload)]
    cases = controlled_cases(cases, selected_hash_profile, args.runtime)
    provenance = InputProvenance(input_paths(args, executable))
    profile = {"runtime": args.runtime, "runtime_executable": cases[0].command[0],
               "classes_directory": str(classes), "workload_names": [case.name for case in cases],
               "workloads": [asdict(case) for case in cases], "execution_profile": "run",
               "runner": str(args.runner_bin), "drrun": str(args.drrun), "client": str(args.client),
               "timeout_ms": args.timeout_ms, "fresh_processes": True, "environment": FIXED_ENV,
               "runtime_image": args.runtime_image, "versions": {}, "host": None, "input_sha256": {}}
    if selected_hash_profile != "isolated":
        profile[args.runtime + "_hash_profile"] = selected_hash_profile
        profile["environment"] = profile_environment(profile)
    plan = fixed_plan(cases, args.repeats)
    by_name = {case.name: case for case in cases}
    rows = [{**item, "state": "unrun", "passed": False, "errors": [],
             "directory": str(args.output_dir / f"repeat-{item['repeat']}" / (item["workload"] + "-" + item["mode"])),
             "command": expected_command(profile, by_name[item["workload"]], item["mode"],
                                         args.output_dir / f"repeat-{item['repeat']}" / (item["workload"] + "-" + item["mode"]))}
            for item in plan]
    collection = {"schema": 1, "authority": "diagnostic_only", "profile": profile, "profile_sha256": None,
                  "options": {"repeats": args.repeats, "max_spread_ppm": args.max_spread_ppm,
                              "campaign_seconds": args.campaign_seconds}, "plan": plan, "rows": rows,
                  "setup_complete": False, "cleanup_complete": False, "inputs_unchanged": False,
                  "host_before": None, "host_after": None, "failures": [],
                  "input_provenance": {"files": provenance.files, "errors": provenance.errors}}
    publish(args.output_dir / "collection.json", collection)
    owner, affinity = None, None
    deadline = time.monotonic() + args.campaign_seconds
    try:
        affinity = os.sched_getaffinity(0)
        cpus = {args.cpu} if args.cpu is not None else {min(affinity)}
        if not cpus <= affinity:
            raise ValueError("requested CPU is outside the allowed affinity")
        os.sched_setaffinity(0, cpus)
        collection["host_before"] = host_profile(cpus)
        profile["host"] = collection["host_before"]
        provenance.capture("before")
        if provenance.errors:
            raise ValueError("required runtime/fixture/tool identity unavailable")
        owner = own_subreaper()
        with fixed_environment(profile["environment"]):
            profile["versions"], _ = workloads.probe_and_compile(args, {args.runtime: executable}, provenance)
            collect_cpython_probe(args, collection, provenance)
            collect_pypy_probe(args, collection, provenance)
            profile["input_sha256"] = {name: item["before"]["sha256"] for name, item in provenance.files.items()}
            collection["profile_sha256"] = digest(profile)
            collection["setup_complete"] = True
            publish(args.output_dir / "collection.json", collection)
            blocked = None
            for row in rows:
                # Preserve one fixed per-process timeout; skip the rest if a full
                # invocation plus collector/cleanup margin no longer fits.
                if not blocked and time.monotonic() + (args.timeout_ms + 15000) / 1000 > deadline:
                    blocked = "campaign deadline leaves insufficient time for the fixed invocation"
                if blocked:
                    row["errors"].append(blocked)
                    continue
                repeat_dir = args.output_dir / f"repeat-{row['repeat']}"
                repeat_dir.mkdir(mode=0o700, exist_ok=True)
                options = SimpleNamespace(**{**vars(args), "output_dir": repeat_dir})
                try:
                    observed = workloads.run_case(options, by_name[row["workload"]], row["mode"])
                    row.update(observed, state="complete")
                    if row.get("cleanup_complete") is not True:
                        blocked = "prior observation descendant cleanup incomplete"
                except (OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError, subprocess.SubprocessError) as error:
                    row.update(state="failed", passed=False)
                    row["errors"].append(f"observation failed: {type(error).__name__}: {error}"[:600])
                    blocked = "prior observation could not complete"
                publish(args.output_dir / "collection.json", collection)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError, subprocess.SubprocessError) as error:
        collection["failures"].append(f"collection setup/owner failed: {type(error).__name__}: {error}"[:600])
    finally:
        if owner is not None:
            libc, previous = owner
            try:
                collection["cleanup_complete"] = cleanup_orphans()
            except OSError:
                collection["cleanup_complete"] = False
            if libc.prctl(36, previous, 0, 0, 0):
                collection["cleanup_complete"] = False
        provenance.capture("after")
        collection["inputs_unchanged"] = provenance.unchanged()
        if affinity is not None:
            try:
                collection["host_after"] = host_profile(cpus)
            except (OSError, ValueError):
                collection["host_after"] = None
            finally:
                try:
                    os.sched_setaffinity(0, affinity)
                except OSError as error:
                    collection["failures"].append("cannot restore harness CPU affinity: " + str(error))
        for row in rows:
            if row["state"] == "unrun" and not row["errors"]:
                row["errors"].append("setup or collection stopped before this planned observation")
        publish(args.output_dir / "collection.json", collection)
    result = evaluate(collection)
    publish(args.output_dir / "evaluation.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    capture = commands.add_parser("collect", help="retain every planned fresh-process observation")
    for name in ("sdk", "client", "runner-bin", "output-dir"):
        capture.add_argument("--" + name, type=Path, required=True)
    capture.add_argument("--runtime", choices=RUNTIMES, required=True)
    capture.add_argument("--cpython-hash-profile", choices=tuple(CPYTHON_HASH_PROFILES), default="isolated",
                         help="explicit single-process diagnostic: isolated (default), controlled random, or seed-1")
    capture.add_argument("--pypy-hash-profile", choices=tuple(PYPY_HASH_PROFILES), default="isolated",
                         help="explicit PyPy diagnostic: isolated (default), or -s -S with random/seed-1; retains current/script import path")
    capture.add_argument("--runtime-bin", help="selected runtime executable; default: java/python3/node/pypy3")
    capture.add_argument("--runtime-file", type=Path, action="append", default=[], help="additional runtime library/module to hash before and after")
    capture.add_argument("--runtime-image", help="operator-supplied immutable sha256 image digest; metadata, not attestation")
    capture.add_argument("--workload", choices=(*workloads.OUTPUTS, *PYTHON_STARTUP_CONTROLS), action="append",
                         help="default: ordinary workloads; CPython/PyPy startup/imports controls require explicit selection")
    capture.add_argument("--javac", default="javac")
    capture.add_argument("--java-major", type=int, choices=(17, 21), default=17)
    capture.add_argument("--cpu", type=bounded_number(0, 1048575), help="one allowed logical CPU; default: first allowed CPU")
    capture.add_argument("--repeats", type=bounded_number(3, 9), default=3)
    capture.add_argument("--max-spread-ppm", type=bounded_number(0, 1_000_000), default=0,
                         help="range/median bound fixed before collection; default 0 means exact repeatability")
    capture.add_argument("--timeout-ms", type=bounded_number(1000, 180000), default=120000)
    capture.add_argument("--campaign-seconds", type=bounded_number(30, 1200), default=600)
    inspect = commands.add_parser("evaluate", help="recompute the recorded gate without executing or changing thresholds")
    inspect.add_argument("--input", type=Path, required=True)
    inspect.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.action == "collect" and args.runtime_image is not None and not re.fullmatch(r"sha256:[0-9a-f]{64}", args.runtime_image):
        parser.error("runtime image must be a lowercase sha256 digest")
    try:
        if args.action == "collect":
            result = collect(args)
        else:
            if args.output.exists():
                raise ValueError("evaluation output must be new")
            result = evaluate(read_collection(args.input))
            publish(args.output, result)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(json.dumps({key: result[key] for key in ("collection_complete", "collection_passed", "qualification_passed", "failures")}))
    return 0 if result["qualification_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
