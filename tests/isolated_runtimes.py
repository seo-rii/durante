#!/usr/bin/env python3
"""Fixed managed workloads and relocated bundles behind the real isolate boundary.

The host owns a finite disposable container; the trusted container driver stays
alive until that owner independently kills and verifies the whole cgroup.
"""
import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import subprocess
import sys
import time
import uuid

from concurrent_spawn import cleanup_orphans, collector, own_subreaper
from execution_owner import (cgroup_state, docker, publish, read_json,
                             stop_owned_container, unified_cgroup)
from runtime_compat import InputProvenance, bounded_bytes, bounded_json
from runtime_workloads import (JAVA_FLAGS, Workload, envelope_errors, same_json,
                               topology_errors, workload_cases)

ROOT = Path(__file__).resolve().parents[1]
CAPTURE_LIMIT = 65536
TIMEOUT_MS = 120000
PROFILES = ("installation", "relocated-bundle")
SUITE_PROFILES = {"all": PROFILES, "java-children": ("relocated-bundle",)}
BUNDLE = Path("/relocated version")
FIXTURE_NAMES = ("RuntimeWorkload.java", "workload.js", "workload.py")
NODE_VERSION = "v22.23.5"


def cpu_set(value):
    """Accept Docker's comma-separated CPU ids and inclusive ranges."""
    if not re.fullmatch(r"[0-9]+(?:-[0-9]+)?(?:,[0-9]+(?:-[0-9]+)?)*", value):
        raise argparse.ArgumentTypeError("use CPU ids or ranges, for example 0,1 or 0-3,6")
    try:
        for part in value.split(","):
            bounds = [int(number) for number in part.split("-")]
            if bounds[0] > bounds[-1]:
                raise ValueError("reversed CPU range")
    except ValueError as error:
        raise argparse.ArgumentTypeError("CPU ranges must use increasing nonnegative ids") from error
    return value


def workload_plan(paths, classes, suite):
    cases = workload_cases(paths, classes)
    if suite == "all":
        return cases
    if suite != "java-children":
        raise ValueError("unknown workload suite")
    children = {case.name: case for case in cases if case.runtime == "java" and case.processes == 3}
    names = ("java-processes-posix_spawn", "java-processes-fork")
    if set(children) != set(names):
        raise ValueError("both Java child launch mechanisms are required")
    return [replace(children[name], name=f"{name}-repeat-{repeat}")
            for repeat in range(1, 4) for name in names]


def resource_snapshot(root=Path("/sys/fs/cgroup")):
    """Read this disposable driver's cgroup, outside the measured application."""
    peak = root / "memory.peak"
    cpuset = root / "cpuset.cpus.effective"
    result = {"memory_current_bytes": int((root / "memory.current").read_text()),
              "memory_peak_bytes": int(peak.read_text()) if peak.exists() else None,
              "memory_max": (root / "memory.max").read_text().strip(),
              "pids_current": int((root / "pids.current").read_text()),
              "pids_max": (root / "pids.max").read_text().strip(),
              "cpu_max": (root / "cpu.max").read_text().strip(),
              "cpuset_cpus_effective": cpuset.read_text().strip() if cpuset.exists() else None}
    for field, name in (("memory_events", "memory.events"), ("pids_events", "pids.events"),
                        ("cpu_stat", "cpu.stat")):
        result[field] = {key: int(value) for key, value in
                         (line.split() for line in (root / name).read_text().splitlines())}
    for field, required in (("memory_events", {"max", "oom", "oom_kill"}),
                            ("pids_events", {"max"}),
                            ("cpu_stat", {"usage_usec", "user_usec", "system_usec"})):
        if not required <= result[field].keys() or any(value < 0 for value in result[field].values()):
            raise ValueError("resource counters incomplete or invalid: " + field)
    return result


def resource_delta(before, after):
    result = {}
    for field in ("memory_events", "pids_events", "cpu_stat"):
        if before[field].keys() != after[field].keys():
            raise ValueError("resource counter set changed: " + field)
        result[field] = {key: after[field][key] - value for key, value in before[field].items()}
        if any(value < 0 for value in result[field].values()):
            raise ValueError("resource counter decreased: " + field)
    return result


def record_observation(directory, row):
    # Keep detailed evidence once; the owner reads the aggregate with a 64-KiB bound.
    evidence = directory.name + "/result.json"
    publish(directory / "result.json", {**row, "resource_evidence": evidence})
    return {**{key: value for key, value in row.items() if key != "resources"},
            "resource_evidence": evidence}


def pipe_run(command, directory, timeout_ms=TIMEOUT_MS):
    """Finite trusted fixture command, using anonymous pipes accepted by isolate."""
    directory.mkdir(mode=0o700)
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    errors = []
    started = time.monotonic()
    with subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, start_new_session=True, cwd=directory,
                          env={"PATH": os.defpath, "LANG": "C", "LC_ALL": "C",
                               "MALLOC_ARENA_MAX": "2"}) as process:
        try:
            with selectors.DefaultSelector() as ready:
                for name in buffers:
                    stream = getattr(process, name)
                    os.set_blocking(stream.fileno(), False)
                    ready.register(stream, selectors.EVENT_READ, name)
                while ready.get_map() or process.poll() is None:
                    if time.monotonic() - started > timeout_ms / 1000:
                        raise TimeoutError("fixture wall deadline expired")
                    for key, _ in ready.select(0.05):
                        data = os.read(key.fd, 8192)
                        if not data:
                            ready.unregister(key.fileobj)
                            continue
                        if len(buffers[key.data]) + len(data) > CAPTURE_LIMIT:
                            raise ValueError("fixture pipe exceeded capture bound")
                        buffers[key.data].extend(data)
                process.wait(timeout=1)
        except (OSError, ValueError, TimeoutError, subprocess.SubprocessError) as error:
            errors.append(str(error))
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=5)
        finally:
            cleaned = cleanup_orphans()
    for name, data in buffers.items():
        (directory / (name + ".txt")).write_bytes(data)
    if not cleaned:
        errors.append("fixture process cleanup incomplete")
    return {"exit_code": process.returncode, "stdout": buffers["stdout"].decode(errors="replace"),
            "stderr": buffers["stderr"].decode(errors="replace"), "errors": errors,
            "cleanup_complete": cleaned, "elapsed_seconds": round(time.monotonic() - started, 3)}


def isolated_errors(report, workload, captured):
    """Require the outer boundary and its diagnostic inner work, independently."""
    errors = []
    contract = collector.measurement_contract()
    if (report.get("authority") != "diagnostic_only" or report.get("integrity_verified") is not False
            or not same_json(report.get("measurement_contract"), contract)
            or report.get("security", {}).get("isolation") != "bubblewrap-0.12-durante-pid1-v1"
            or report.get("security", {}).get("counter_integrity") != "application_mutable"):
        errors.append("outer isolation or diagnostic contract missing")
    execution = report.get("execution", {})
    if (execution.get("state") != "complete" or type(execution.get("wrapper_exit_code")) is not int
            or execution["wrapper_exit_code"] != 0 or execution.get("cleanup_complete") is not True
            or execution.get("scratch_cleanup_complete") is not True):
        errors.append("outer execution or cleanup incomplete")
    output = report.get("output", {})
    if (output.get("mode") != "captured" or output.get("published") is not True
            or output.get("complete") is not True or output.get("truncated") is not False
            or output.get("limit_bytes") != CAPTURE_LIMIT
            or output.get("stdout_captured_bytes") != len(captured[0])
            or output.get("stderr_captured_bytes") != len(captured[1])
            or captured != (workload.expected.encode(), b"")):
        errors.append("captured output differs from the exact fixture oracle")
    inner = report.get("untrusted_inner_report", {})
    errors.extend(envelope_errors(inner))
    measurement = inner.get("measurement", {})
    if (measurement.get("valid") is not True or measurement.get("complete") is not True
            or measurement.get("authority") != "diagnostic_only"
            or measurement.get("integrity_verified") is not False
            or not same_json(measurement.get("measurement_contract"), contract)
            or measurement.get("rep_bytes_enabled") is not True
            or measurement.get("quality") not in ("uninterrupted", "signals_observed")
            or type(measurement.get("instructions")) is not int or measurement["instructions"] <= 0
            or type(measurement.get("process_count")) is not int or measurement["process_count"] != workload.processes
            or type(measurement.get("image_count")) is not int or measurement["image_count"] != workload.images):
        errors.append("complete diagnostic process-tree work unavailable")
    else:
        errors.extend(topology_errors(measurement, inner.get("execution", {}).get("root_pid"), workload))
        for key in ("instructions", "rep_bytes", "rep_entries", "unsupported_rep_entries", "signals_observed"):
            values = [measurement.get(key), *(row.get(key) for row in measurement.get("processes", [])),
                      *(row.get(key) for row in measurement.get("segments", []))]
            if any(type(value) is not int or not 0 <= value < 1 << 64 for value in values):
                errors.append("invalid diagnostic counter type/range")
            elif (measurement[key] != sum(row[key] for row in measurement["processes"])
                    or measurement[key] != sum(row[key] for row in measurement["segments"])):
                errors.append("process/image work totals disagree")
    return errors


def matrix_errors(cases, rows):
    expected = {(case.name, mode) for case in cases for mode in ("native", "isolate")}
    observed = [(row["workload"], row["mode"]) for row in rows]
    if not expected or len(expected) != len(observed) or set(observed) != expected:
        return ["required native/isolate matrix is incomplete or duplicated"]
    return [row["workload"] + "/" + row["mode"] + " failed" for row in rows if not row["passed"]]


def owner_errors(observation, teardown):
    errors = []
    if observation.get("passed") is not True or observation.get("cleanup_complete") is not True:
        errors.append("runtime profile failed: " + "; ".join(observation.get("errors", [])))
    if any(teardown.get(key) is not True for key in (
            "cleanup_complete", "container_removed", "live_before_owner_stop", "owner_kill_succeeded")):
        errors.append("independent live-job termination or cgroup cleanup incomplete")
    return errors


def installation_prefix(profile, command):
    if profile == "relocated-bundle":
        return [str(BUNDLE / "bin/durante-bundle"), "doctor-isolate" if command == "doctor" else command]
    return ["/installation/durante", command, *( ["--profile", "isolate"] if command == "doctor" else []),
            "--drrun", "/sdk/bin64/drrun", "--client", "/client/libdurante.so",
            "--launcher", "/isolation-tool/bwrap"]


def relocated_libraries(data):
    loaded = {}
    for library in ("libdurante.so", "libdrmgr.so", "libdynamorio.so"):
        paths = {line.split(maxsplit=5)[5].strip() for line in data.decode().splitlines()
                 if len(line.split(maxsplit=5)) == 6 and line.endswith("/" + library)}
        if not paths or any(not Path(path).is_relative_to(BUNDLE) for path in paths):
            raise ValueError("runtime library resolved outside relocated bundle: " + library)
        loaded[library] = sorted(paths)
    return loaded


def worker(profile, java_major, suite="all"):
    evidence = Path("/evidence")
    rows, errors, versions = [], [], {}
    libc, previous = own_subreaper()
    result = {"schema": 1, "profile": profile, "suite": suite, "passed": False, "rows": rows,
              "planned_observations": [],
              "authority": "diagnostic_only", "integrity_verified": False, "versions": versions}
    try:
        if profile == "relocated-bundle" and any(Path(path).exists() for path in (
                "/sdk", "/client", "/installation", "/isolation-tool")):
            raise ValueError("original installation is visible in relocated container")
        result["original_installation_absent"] = profile == "relocated-bundle"
        workspace = evidence / "input"
        workspace.mkdir(mode=0o700)
        classes = workspace / "classes"
        classes.mkdir()
        for name in FIXTURE_NAMES:
            shutil.copyfile(ROOT / "tests/runtime_workload_fixtures" / name, workspace / name)
        if profile == "relocated-bundle":
            shutil.copyfile(ROOT / "bundle-probe", workspace / "MapProbe")
            (workspace / "MapProbe").chmod(0o700)
        shutil.copyfile(ROOT / "ioctl-probe", workspace / "IoctlProbe")
        (workspace / "IoctlProbe").chmod(0o700)
        paths = {"java": str(Path("/usr/bin/java").resolve(strict=True)),
                 "node": str(Path("/usr/bin/node").resolve(strict=True)),
                 "cpython": str(Path("/usr/bin/python3").resolve(strict=True))}
        for runtime, executable in paths.items():
            command = [executable, *JAVA_FLAGS, "-version"] if runtime == "java" else [executable, "--version"]
            row = pipe_run(command, evidence / ("version-" + runtime), 15000)
            if row["exit_code"] or row["errors"]:
                raise ValueError("required runtime unavailable: " + runtime)
            versions[runtime] = (row["stdout"] + row["stderr"]).strip()
        if not versions["java"].startswith(f'openjdk version "{java_major}.'):
            raise ValueError("OpenJDK major differs from the required image profile")
        if versions["node"] != NODE_VERSION:
            raise ValueError("Node version differs from the pinned self-contained runtime")
        compiled = pipe_run(["/usr/bin/javac", "-J-Xmx128m", "-J-XX:ActiveProcessorCount=2", "-d", str(classes),
                             str(workspace / "RuntimeWorkload.java")], evidence / "compile", 30000)
        if compiled["exit_code"] or compiled["errors"] or compiled["stderr"]:
            raise ValueError("fixed Java workload compilation failed")
        cases = []
        for case in workload_plan(paths, classes, suite):
            command = [str(workspace / Path(arg).name) if arg.startswith(str(ROOT / "tests/runtime_workload_fixtures"))
                       else arg for arg in case.command]
            cases.append(replace(case, command=command))
        result["planned_observations"] = [{"workload": case.name, "mode": mode}
                                           for case in cases for mode in ("native", "isolate")]
        publish(evidence / "plan.json", {"suite": suite, "profile": profile,
                                        "observations": result["planned_observations"]})
        doctor = pipe_run(installation_prefix(profile, "doctor"), evidence / "doctor", 15000)
        result["doctor"] = json.loads(doctor["stdout"])
        if (doctor["exit_code"] or doctor["errors"] or doctor["stderr"]
                or result["doctor"].get("checks_passed") is not True
                or result["doctor"].get("execution_tested") is not False):
            raise ValueError("real isolate prerequisites failed")
        for case in cases:
            for mode in ("native", "isolate"):
                directory = evidence / (case.name + "-" + mode)
                if mode == "native":
                    command = ["/usr/bin/prlimit", "--as=1073741824", "--cpu=121", "--fsize=16777216", "--core=0",
                               "--", *case.command]
                else:
                    target = [arg.replace(str(workspace), "/work") for arg in case.command]
                    command = [*installation_prefix(profile, "isolate"), "--workspace", str(workspace),
                               "--report", str(directory / "report.json"), "--output-dir", str(directory / "captured"),
                               "--output-limit-bytes", str(CAPTURE_LIMIT), "--timeout-ms", str(TIMEOUT_MS), "--", *target]
                before = resource_snapshot()
                row = pipe_run(command, directory, TIMEOUT_MS + 5000)
                row.update(workload=case.name, runtime=case.runtime, mode=mode, command=command)
                row["resources"] = {"before": before}
                try:
                    row["resources"]["after"] = resource_snapshot()
                    row["resources"]["delta"] = resource_delta(before, row["resources"]["after"])
                except (OSError, ValueError, KeyError, TypeError) as error:
                    row["errors"].append("resource evidence unavailable: " + str(error)[:500])
                if row["exit_code"] != 0:
                    row["errors"].append("fixture command failed")
                if mode == "native":
                    if row["stdout"] != case.expected or row["stderr"]:
                        row["errors"].append("native exact-output control failed")
                else:
                    try:
                        captured = tuple(bounded_bytes(directory / "captured" / name) for name in ("stdout", "stderr"))
                        report = bounded_json(directory / "report.json")
                        row["errors"].extend(isolated_errors(report, case, captured))
                        if row["stdout"] or row["stderr"]:
                            row["errors"].append("outer controller emitted unexpected diagnostics")
                        row["instructions"] = report.get("untrusted_inner_report", {}).get("measurement", {}).get("instructions")
                    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
                        row["errors"].append("isolation evidence unavailable: " + str(error)[:500])
                row["passed"] = not row["errors"]
                rows.append(record_observation(directory, row))
        errors.extend(matrix_errors(cases, rows))
        directory = evidence / "ioctl-queries"
        command = [*installation_prefix(profile, "isolate"), "--workspace", str(workspace),
                   "--report", str(directory / "report.json"), "--output-dir", str(directory / "captured"),
                   "--output-limit-bytes", str(CAPTURE_LIMIT), "--timeout-ms", "5000", "--", "/work/IoctlProbe"]
        ioctl_probe = pipe_run(command, directory, 10000)
        captured = tuple(bounded_bytes(directory / "captured" / name) for name in ("stdout", "stderr"))
        report = bounded_json(directory / "report.json")
        ioctl_probe["errors"].extend(isolated_errors(report,
            Workload("ioctl-queries", "native", [], "descriptor-ioctl\n", 1, 1), captured))
        if ioctl_probe["exit_code"] or ioctl_probe["stdout"] or ioctl_probe["stderr"]:
            ioctl_probe["errors"].append("descriptor ioctl probe failed")
        result["ioctl_probe"] = ioctl_probe
        errors.extend(ioctl_probe["errors"])
        if profile == "relocated-bundle":
            directory = evidence / "mapped-libraries"
            command = [*installation_prefix(profile, "isolate"), "--workspace", str(workspace),
                       "--report", str(directory / "report.json"), "--output-dir", str(directory / "captured"),
                       "--output-limit-bytes", str(CAPTURE_LIMIT), "--timeout-ms", "5000", "--", "/work/MapProbe"]
            probe = pipe_run(command, directory, 10000)
            captured = tuple(bounded_bytes(directory / "captured" / name) for name in ("stdout", "stderr"))
            report = bounded_json(directory / "report.json")
            probe["errors"].extend(isolated_errors(report,
                Workload("mapped-libraries", "native", [], captured[0].decode(), 1, 1), captured))
            if probe["exit_code"] or probe["stdout"] or probe["stderr"]:
                probe["errors"].append("relocated map probe failed")
            result["loaded_libraries"] = relocated_libraries(captured[0])
            result["map_probe"] = probe
            errors.extend(probe["errors"])
        result["passed"] = not errors
    except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError) as error:
        errors.append(str(error)[:2000])
    finally:
        result["cleanup_complete"] = cleanup_orphans()
        libc.prctl(36, previous, 0, 0, 0)
        observed = {(row["workload"], row["mode"]) for row in rows}
        result["unrun_observations"] = [row for row in result["planned_observations"]
                                        if (row["workload"], row["mode"]) not in observed]
        result.update(errors=errors, cases=len(rows), passed=result["passed"] and result["cleanup_complete"])
        publish(evidence / "worker.json", result)
    # Deliberately remain alive, independently of Durante, until the external
    # owner kills the job. A finite fallback must not qualify as owner recovery.
    time.sleep(120)
    return 1


def prepare_bundle(args):
    archive = args.work_dir / "bundle.tar.gz"
    command = [sys.executable, str(ROOT / "toolkit/bundle.py"), "pack", "--sdk", str(args.sdk),
               "--cli", str(args.cli), "--client", str(args.client), "--launcher", str(args.launcher),
               "--output", str(args.work_dir / "packed"), "--archive", str(archive)]
    packed = subprocess.run(command, capture_output=True, timeout=60, check=True)
    metadata = json.loads(packed.stdout)
    unpacked = args.work_dir / "unpacked"
    subprocess.run([sys.executable, str(ROOT / "toolkit/bundle.py"), "unpack", "--archive", str(archive),
                    "--sha256", metadata["archive_sha256"], "--source-revision", metadata["source_revision"],
                    "--output", str(unpacked)], capture_output=True, timeout=60, check=True)
    relocated = args.work_dir / "relocated version"
    unpacked.rename(relocated)
    publish(args.output_dir / "bundle.json", metadata)
    shutil.copyfile(relocated / "durante-bundle.json", args.output_dir / "bundle-manifest.json")
    return relocated


def prepare_driver(args):
    # Mount a closed harness-only tree. Mounting the checkout would accidentally
    # make CI's toolkit/sdk and build directories reachable under /source.
    driver = args.work_dir / "driver-source"
    (driver / "tests/runtime_workload_fixtures").mkdir(parents=True, mode=0o700)
    for path in (ROOT / "tests").glob("*.py"):
        shutil.copyfile(path, driver / "tests" / path.name)
    for path in (ROOT / "tests/runtime_workload_fixtures" / name for name in FIXTURE_NAMES):
        shutil.copyfile(path, driver / "tests/runtime_workload_fixtures" / path.name)
    shutil.copyfile(ROOT / "collect.py", driver / "collect.py")
    for source, target in (("bundle_probe.c", "bundle-probe"), ("isolation_ioctl_probe.c", "ioctl-probe")):
        defines = ["-D_GNU_SOURCE"] if source == "bundle_probe.c" else []
        subprocess.run(["cc", "-pipe", "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", *defines,
                        str(ROOT / "tests" / source), "-o", str(driver / target)],
                       capture_output=True, timeout=30, check=True)
    return driver


def execution_inputs(driver, relocated):
    inputs = {}
    for path in driver.rglob("*"):
        if path.is_file():
            inputs["driver/" + path.relative_to(driver).as_posix()] = path
    for name in ("durante-bundle.json", "bin/durante", "bin/durante-bundle", "bin/bwrap",
                 "bin/bwrap.durante-isolation.json", "lib/libdurante.so", "sdk/.durante-runtime.json",
                 "sdk/bin64/drrun", "sdk/lib64/release/libdynamorio.so", "sdk/ext/lib64/release/libdrmgr.so"):
        inputs["bundle/" + name] = relocated / name
    return inputs


def execute_profile(args, profile, relocated):
    directory = args.output_dir / profile
    directory.mkdir(mode=0o700)
    name = "durante-isolated-runtimes-" + uuid.uuid4().hex
    identity, group, observation = None, None, {}
    errors = []
    row = {"profile": profile, "suite": args.suite, "requested_cpuset_cpus": args.cpuset_cpus,
           "owner_outside_job": False, "passed": False}
    try:
        mounts = [(args.driver, "/source", True), (directory, "/evidence", False)]
        if profile == "relocated-bundle":
            mounts.append((relocated, str(BUNDLE), True))
        else:
            mounts += [(args.cli.parent, "/installation", True), (args.sdk, "/sdk", True),
                       (args.client.parent, "/client", True), (args.launcher.parent, "/isolation-tool", True)]
        command = ["create", "--name", name, "--network", "none", "--read-only",
                   "--user", f"{os.getuid()}:{os.getgid()}", "--memory", "1g", "--pids-limit", "128",
                   "--cpus", "2", "--cgroupns", "private", "--security-opt", "systempaths=unconfined",
                   "--security-opt", "seccomp=unconfined", "--security-opt", "apparmor=unconfined",
                   "--log-driver", "none", "--tmpfs", "/tmp:rw,exec,mode=1777,size=268435456",
                   "--env", "PYTHONDONTWRITEBYTECODE=1"]
        if args.cpuset_cpus is not None:
            command += ["--cpuset-cpus", args.cpuset_cpus]
        for source, target, readonly in mounts:
            command += ["--mount", f"type=bind,src={source},dst={target}" + (",readonly" if readonly else "")]
        command += ["--entrypoint", "/usr/bin/python3", args.image, "/source/tests/isolated_runtimes.py",
                    "--worker", profile, "--java-major", str(args.java_major), "--suite", args.suite]
        identity = docker(*command).stdout.decode().strip()
        if len(identity) != 64 or any(c not in "0123456789abcdef" for c in identity):
            raise ValueError("unexpected disposable container identity")
        row["container_id"] = identity
        docker("start", identity)
        state = json.loads(docker("inspect", "--format", "{{json .State}}", identity).stdout)
        if not state["Running"] or state["Pid"] <= 1:
            raise ValueError("runtime driver failed before cgroup observation")
        job = unified_cgroup(Path(f"/proc/{state['Pid']}/cgroup").read_text())
        own = unified_cgroup(Path("/proc/self/cgroup").read_text())
        if job == "/" or job == own or own.startswith(job + "/"):
            raise ValueError("owner is not outside the job cgroup")
        group = Path("/sys/fs/cgroup") / job.lstrip("/")
        row.update(owner_outside_job=True, job_cgroup=job, cgroup_before=cgroup_state(group))
        row["resources"] = {"before": resource_snapshot(group)}
        if args.cpuset_cpus is not None and not row["resources"]["before"]["cpuset_cpus_effective"]:
            raise ValueError("requested CPU placement has no effective cpuset evidence")
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            if (directory / "worker.json").exists():
                observation = read_json(directory / "worker.json")
                break
            state = json.loads(docker("inspect", "--format", "{{json .State}}", identity).stdout)
            if not state["Running"]:
                row["container_exit"] = {key: state[key] for key in ("ExitCode", "OOMKilled")}
                raise ValueError("runtime driver exited without complete evidence")
            time.sleep(1)
        else:
            raise TimeoutError("external owner deadline expired")
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
        errors.append(str(error)[:2000])
    finally:
        if group is not None:
            try:
                row.setdefault("resources", {})["after"] = resource_snapshot(group)
                if "before" in row["resources"]:
                    row["resources"]["delta"] = resource_delta(
                        row["resources"]["before"], row["resources"]["after"])
            except (OSError, ValueError, KeyError, TypeError) as error:
                errors.append("owner resource evidence unavailable: " + str(error)[:500])
        teardown = stop_owned_container(identity or name, group)
        errors.extend(teardown.pop("errors"))
        row.update(teardown)
        errors.extend(owner_errors(observation, teardown))
    row.update(errors=errors, observation=observation, passed=not errors)
    publish(directory / "owner.json", row)
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", choices=PROFILES)
    parser.add_argument("--image")
    parser.add_argument("--java-major", type=int, required=True)
    parser.add_argument("--suite", choices=tuple(SUITE_PROFILES), default="all",
                        help="all workloads, or twelve fixed relocated Java child observations")
    parser.add_argument("--cpuset-cpus", type=cpu_set,
                        help="optional Docker CPU ids/ranges for reproduction; the two-CPU quota stays fixed")
    for name in ("cli", "sdk", "client", "launcher", "output-dir", "work-dir"):
        parser.add_argument("--" + name, type=Path)
    args = parser.parse_args()
    if args.worker:
        if args.worker not in SUITE_PROFILES[args.suite]:
            parser.error("worker profile is not part of the selected suite")
        return worker(args.worker, args.java_major, args.suite)
    if not args.image or any(getattr(args, name) is None for name in (
            "cli", "sdk", "client", "launcher", "output_dir", "work_dir")):
        parser.error("image, installation and fresh output/work directories are required")
    if os.getuid() == 0:
        parser.error("run as the nonroot isolation operator")
    for name in ("cli", "sdk", "client", "launcher"):
        setattr(args, name, getattr(args, name).resolve(strict=True))
    if args.cli.name != "durante" or args.launcher.name != "bwrap" or args.client.name != "libdurante.so":
        parser.error("the installation must use its conventional component names")
    args.output_dir = args.output_dir.absolute()
    args.work_dir = args.work_dir.absolute()
    if (args.work_dir == args.output_dir or args.work_dir.is_relative_to(args.output_dir)
            or args.output_dir.is_relative_to(args.work_dir)):
        parser.error("work and evidence directories must be separate")
    args.output_dir.mkdir(mode=0o700)
    args.work_dir.mkdir(mode=0o700)
    args.image = docker("image", "inspect", "--format", "{{.Id}}", args.image).stdout.decode().strip()
    inputs = {"cli": args.cli, "client": args.client, "launcher": args.launcher,
              "launcher_manifest": args.launcher.with_name("bwrap.durante-isolation.json"),
              "runtime_manifest": args.sdk / ".durante-runtime.json"}
    for path in sorted((ROOT / "tests").glob("*.py")):
        inputs["harness/" + path.name] = path
    for path in (ROOT / "tests/runtime_workload_fixtures" / name for name in FIXTURE_NAMES):
        inputs["fixture/" + path.name] = path
    inputs["fixture/bundle_probe.c"] = ROOT / "tests/bundle_probe.c"
    inputs["fixture/isolation_ioctl_probe.c"] = ROOT / "tests/isolation_ioctl_probe.c"
    inputs["collector"] = ROOT / "collect.py"
    inputs["image_recipe"] = ROOT / "docker/isolated-runtimes.Dockerfile"
    for name, digest in json.loads((args.sdk / ".durante-runtime.json").read_text())["artifacts"].items():
        if digest is not None:
            inputs["runtime/" + name] = args.sdk / name
    for name in ("bin64/drrun", "ext/lib64/release/libdrmgr.so"):
        inputs["runtime/" + name] = args.sdk / name
    provenance = InputProvenance(inputs)
    provenance.capture("before")
    if provenance.errors:
        publish(args.output_dir / "preflight.json", {"errors": provenance.errors})
        raise ValueError("cannot establish stable test inputs")
    relocated = prepare_bundle(args)
    args.driver = prepare_driver(args)
    executed = InputProvenance(execution_inputs(args.driver, relocated))
    executed.capture("before")
    if executed.errors:
        publish(args.output_dir / "preflight.json", {"errors": executed.errors})
        raise ValueError("cannot establish stable executed inputs")
    rows = [execute_profile(args, profile, relocated) for profile in SUITE_PROFILES[args.suite]]
    provenance.capture("after")
    executed.capture("after")
    result = {"schema": 1, "image_id": args.image, "java_major": args.java_major, "suite": args.suite,
              "authority": "diagnostic_only", "integrity_verified": False, "rows": rows,
              "passed": all(row["passed"] for row in rows) and not provenance.errors and not executed.errors,
              "cleanup_complete": all(row.get("cleanup_complete") is True for row in rows),
              "provenance": provenance.files, "provenance_errors": provenance.errors,
              "executed_provenance": executed.files, "executed_provenance_errors": executed.errors,
              "limits": ["fixed distro runtime image and small heaps only", "counts remain application-mutable",
                         "no raw-record export from the inner sandbox", "not arbitrary runtime or workload support"]}
    publish(args.output_dir / "results.json", result)
    print(json.dumps({key: result[key] for key in ("passed", "cleanup_complete", "image_id", "java_major", "suite")}))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
