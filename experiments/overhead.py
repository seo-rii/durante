#!/usr/bin/env python3
"""Bounded diagnostic overhead samples for three fixed Linux x86-64 fixtures.

Measures the entire native/drrun invocation, including runtime setup, client
initialization and report writes. Collection and fixture compilation are outside
the timed interval. This is not an arbitrary command runner or a performance gate.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import resource
import selectors
import shutil
import signal
import stat
import statistics
import struct
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from concurrent_spawn import cleanup_orphans, collector, own_subreaper

MODES = ("native", "engine", "counter_rep_off", "counter_rep_on")
FIXTURES = {
    "startup": {"define": 0, "instructions": 8, "output": 0x0123456789abcdef,
                "rep_entries": 0, "rep_bytes": 0},
    "loop": {"define": 1, "instructions": 4_000_011, "output": 6_000_000,
             "rep_entries": 0, "rep_bytes": 0},
    "rep": {"define": 2, "instructions": 1292, "output": 0x5a5a5a5a5a5a5a5a,
            "rep_entries": 256, "rep_bytes": 16_777_216},
}
METRICS = ("wall_seconds", "user_seconds", "system_seconds", "cpu_seconds", "peak_rss_kib")
MAX_CAPTURE = 64 << 10
CHILD_ENV = {"PATH": os.defpath, "LANG": "C", "LC_ALL": "C"}


def child_limits(timeout):
    # Trusted tools and fixed fixtures only. These are backstops, not a sandbox.
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (1 << 20, 1 << 20))
    resource.setrlimit(resource.RLIMIT_AS, (1 << 30, 1 << 30))
    resource.setrlimit(resource.RLIMIT_CPU, (math.ceil(timeout) + 1, math.ceil(timeout) + 1))


def run_timed(command, timeout=5):
    """Consume wait4 exactly once for this child; never use cumulative rusage."""
    row = {"pid": None, "exit_code": None, "timed_out": False, "output_limit": False,
           "reaped": False, "error": None, "metrics": None}
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    selector = selectors.DefaultSelector()
    process = usage = None
    started = time.monotonic_ns()
    deadline = time.monotonic() + timeout
    try:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, start_new_session=True, env=CHILD_ENV,
                                   umask=0o077, preexec_fn=lambda: child_limits(timeout))
        row["pid"] = process.pid
        for label, stream in (("stdout", process.stdout), ("stderr", process.stderr)):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, label)
        while not row["reaped"] or selector.get_map():
            if not row["reaped"]:
                pid, status, observed = os.wait4(process.pid, os.WNOHANG)
                if pid:
                    process.returncode = os.waitstatus_to_exitcode(status)
                    row["exit_code"], row["reaped"] = process.returncode, True
                    usage = observed
                    row["metrics"] = {
                        "wall_seconds": (time.monotonic_ns() - started) / 1e9,
                        "user_seconds": usage.ru_utime, "system_seconds": usage.ru_stime,
                        "cpu_seconds": usage.ru_utime + usage.ru_stime,
                        "peak_rss_kib": usage.ru_maxrss,
                    }
            if row["reaped"] and not selector.get_map():
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                row["timed_out"] = True
                break
            if not selector.get_map():
                time.sleep(min(remaining, 0.001))
                continue
            for key, _ in selector.select(min(remaining, 0.005)):
                data = os.read(key.fileobj.fileno(), 65536)
                if not data:
                    selector.unregister(key.fileobj)
                    key.fileobj.close()
                    continue
                buffer = buffers[key.data]
                available = MAX_CAPTURE - len(buffer)
                buffer.extend(data[:available])
                if len(data) > available:
                    row["output_limit"] = True
                    break
            if row["output_limit"]:
                break
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        row["error"] = str(error)[:500]
    finally:
        if process is not None:
            if not row["reaped"]:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                until = time.monotonic() + 1
                while time.monotonic() < until:
                    pid, status, _ = os.wait4(process.pid, os.WNOHANG)
                    if pid:
                        process.returncode = os.waitstatus_to_exitcode(status)
                        row["exit_code"], row["reaped"] = process.returncode, True
                        break
                    time.sleep(0.005)
                if not row["reaped"]:
                    row["error"] = "child did not exit after termination"
            for stream in (process.stdout, process.stderr):
                if not stream.closed:
                    stream.close()
        selector.close()
    row.update({name: bytes(data) for name, data in buffers.items()})
    return row


def process_error(row):
    if row["error"] or row["timed_out"] or row["output_limit"] or not row["reaped"]:
        return "execution, output bound or cleanup failed"
    if row["exit_code"] != 0 or row["stderr"]:
        return "unexpected exit status or diagnostics"
    metrics = row["metrics"]
    if type(metrics) is not dict or any(type(metrics.get(key)) not in (int, float) or
            not math.isfinite(metrics[key]) or metrics[key] < 0 for key in METRICS):
        return "missing or invalid per-child resource usage"
    return None


def run_sample(timer, usage_file, command, timeout=5):
    # The outer wait4 belongs to the timer, so its metrics are deliberately
    # discarded. The timer reports its own child's usage without Python's RSS.
    row = run_timed([str(timer), str(usage_file), str(round(timeout * 1000)), *command], timeout + 2)
    row["launcher"] = {key: row[key] for key in ("pid", "exit_code", "reaped", "timed_out")}
    row.update(pid=None, exit_code=None, reaped=False, metrics=None)
    try:
        with usage_file.open("rb") as stream:
            data = stream.read(4097)
        if len(data) > 4096:
            raise ValueError("oversized timer result")
        usage = json.loads(data)
        fields = {"schema", "pid", "reaped", "timed_out", "exit_code", "error",
                  "wall_ns", "user_us", "system_us", "peak_rss_kib"}
        if type(usage) is not dict or set(usage) != fields or type(usage["schema"]) is not int or usage["schema"] != 1:
            raise ValueError("invalid timer schema")
        for key in ("pid", "exit_code", "error", "wall_ns", "user_us", "system_us", "peak_rss_kib"):
            if type(usage.get(key)) is not int:
                raise ValueError("invalid timer value: " + key)
        if any(not 0 <= usage[key] <= (1 << 63) - 1 for key in ("wall_ns", "user_us", "system_us", "peak_rss_kib")):
            raise ValueError("negative timer usage")
        if not 0 < usage["pid"] <= (1 << 31) - 1 or type(usage.get("reaped")) is not bool or type(usage.get("timed_out")) is not bool:
            raise ValueError("invalid timer child identity or completion")
        if not -64 <= usage["exit_code"] <= 255 or usage["error"] < 0:
            raise ValueError("invalid timer status")
        if row["launcher"]["exit_code"] != (0 if usage["reaped"] and not usage["timed_out"] and
                usage["error"] == 0 and usage["exit_code"] == 0 else 1):
            raise ValueError("timer exit disagrees with its child result")
        row.update(pid=usage["pid"], exit_code=usage["exit_code"],
                   reaped=row["launcher"]["reaped"] and usage["reaped"],
                   timed_out=row["timed_out"] or usage["timed_out"])
        row["error"] = row["error"] or ("timer infrastructure failed" if usage["error"] else None)
        row["metrics"] = {"wall_seconds": usage["wall_ns"] / 1e9,
                          "user_seconds": usage["user_us"] / 1e6,
                          "system_seconds": usage["system_us"] / 1e6,
                          "cpu_seconds": (usage["user_us"] + usage["system_us"]) / 1e6,
                          "peak_rss_kib": usage["peak_rss_kib"]}
    except (OSError, ValueError, UnicodeError) as error:
        row["error"] = "timer result unavailable: " + str(error)[:500]
    return row


def measurement_error(report, fixture, mode):
    if type(report) is not dict:
        return "collector did not return an object"
    enabled = mode == "counter_rep_on"
    expected = FIXTURES[fixture]
    fields = {"schema": 1, "authority": "diagnostic_only", "integrity_verified": False,
              "valid": True, "complete": True, "quality": "uninterrupted",
              "metric": "bb_entry_app_instructions", "scope": "process_tree_user_code",
              "rep_bytes_enabled": enabled, "process_count": 1, "image_count": 1,
              "instructions": expected["instructions"], "signals_observed": 0,
              "unsupported_rep_entries": 0,
              "rep_entries": expected["rep_entries"] if enabled else 0,
              "rep_bytes": expected["rep_bytes"] if enabled else 0,
              "rep_telemetry_status": "movs_stos_only" if enabled else "disabled"}
    for key, value in fields.items():
        if type(report.get(key)) is not type(value) or report[key] != value:
            return "accounting oracle failed: " + key
    if report.get("measurement_contract") != collector.measurement_contract():
        return "measurement contract missing or promoted"
    return None


def inspect_matrix(rows, warmups, repeats):
    expected = {(fixture, mode, phase, index) for fixture in FIXTURES for mode in MODES
                for phase, count in (("warmup", warmups), ("sample", repeats))
                for index in range(count)}
    observed = set()
    for row in rows:
        key = (row.get("fixture"), row.get("mode"), row.get("phase"), row.get("index"))
        if key not in expected or key in observed or row.get("passed") is not True:
            return "failed, duplicate or unexpected observation"
        observed.add(key)
    return None if observed == expected else "incomplete observation matrix"


def distribution(values):
    return {"samples": values, "median": statistics.median(values), "min": min(values),
            "max": max(values), "range": max(values) - min(values)}


def summarize(rows):
    result = {}
    for fixture in FIXTURES:
        groups = {}
        for mode in MODES:
            samples = [row for row in rows if row["fixture"] == fixture and
                       row["mode"] == mode and row["phase"] == "sample"]
            group = {key: distribution([row["metrics"][key] for row in samples]) for key in METRICS}
            if mode.startswith("counter_"):
                group["instructions"] = distribution([row["measurement"]["instructions"] for row in samples])
                group["instructions"]["distinct"] = len(set(group["instructions"]["samples"]))
            groups[mode] = group
        ratios = {mode: {key: groups[mode][key]["median"] / groups["native"][key]["median"]
                         if groups["native"][key]["median"] > 0 else None for key in METRICS}
                  for mode in MODES[1:]}
        result[fixture] = {"distributions": groups, "median_ratios_to_native": ratios}
    return result


def finalize(summary):
    summary["inputs_unchanged"] = ("inputs_before" in summary and
        summary["inputs_before"] == summary.get("inputs_after"))
    summary["executables_unchanged"] = ("executables_before" in summary and
        summary["executables_before"] == summary.get("executables_after"))
    error = summary.get("failure")
    if not summary["inputs_unchanged"] or not summary["executables_unchanged"]:
        error = error or "experiment inputs changed or could not be verified"
    if summary.get("cleanup_complete") is not True:
        error = error or "descendant cleanup incomplete"
    error = error or inspect_matrix(summary["rows"], summary["warmups"], summary["repeats"])
    summary["failure"], summary["passed"] = error, error is None
    summary["statistics"] = summarize(summary["rows"]) if summary["passed"] else {}


def hash_file(path):
    digest = hashlib.sha256()
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > 64 << 20:
            raise ValueError("identity input must be a regular file of at most 64 MiB")
        read = 0
        while chunk := stream.read(min(1 << 20, (64 << 20) + 1 - read)):
            read += len(chunk)
            if read > 64 << 20:
                raise ValueError("identity input grew beyond its size bound")
            digest.update(chunk)
        after = os.fstat(stream.fileno())
        if read != before.st_size or any(getattr(before, field) != getattr(after, field)
                for field in ("st_size", "st_mtime_ns", "st_ctime_ns")):
            raise ValueError("identity input changed while hashing")
    return {"path": str(path.resolve()), "sha256": digest.hexdigest(), "bytes": read}


def host_identity(cpu):
    blocks = Path("/proc/cpuinfo").read_text().split("\n\n")
    entries = [dict((key.strip(), value.strip()) for line in block.splitlines() if ":" in line
                    for key, value in [line.split(":", 1)]) for block in blocks]
    entry = next(row for row in entries if row.get("processor") == str(cpu))
    wanted = ("processor", "vendor_id", "cpu family", "model", "model name", "stepping",
              "microcode", "physical id", "core id", "cache size", "cpu cores", "siblings", "flags")
    policy = {}
    for name in ("scaling_governor", "scaling_driver", "scaling_min_freq", "scaling_max_freq"):
        path = Path(f"/sys/devices/system/cpu/cpu{cpu}/cpufreq") / name
        policy[name] = path.read_text().strip() if path.is_file() else None
    return {"system": platform.system(), "machine": platform.machine(),
            "kernel": platform.release(), "kernel_version": platform.version(),
            "cpu": {key: entry.get(key) for key in wanted},
            "frequency_policy": policy, "libc": list(platform.libc_ver()),
            "affinity": sorted(os.sched_getaffinity(0))}


def identities(paths, cpu):
    if len(paths) > 128 or sum(path.stat().st_size for path in paths.values()) > 256 << 20:
        raise ValueError("identity inventory exceeds file count or total byte bound")
    return {"files": {name: hash_file(path) for name, path in sorted(paths.items())},
            "host": host_identity(cpu),
            "source_revision": tool_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"])}


def tool_output(command):
    row = run_timed(command, timeout=30)
    if process_error(row):
        raise ValueError("tool failed: " + str(command[0]) + ": " +
                         row["stderr"].decode(errors="replace")[:500])
    return row["stdout"].decode().strip()


def command_for(mode, program, drrun, client, records):
    if mode == "native":
        return [str(program)]
    command = [str(drrun), "-quiet", "-follow_children", "-vm_size", "32M", "-vmheap_size", "32M"]
    if mode.startswith("counter_"):
        command += ["-c", str(client), "-outdir", str(records)]
        if mode == "counter_rep_on":
            command.append("-rep_bytes")
    return [*command, "--", str(program)]


def observe(args, directory, program, fixture, mode, phase, index):
    records = directory / "records"
    directory.mkdir(mode=0o700)
    if mode.startswith("counter_"):
        records.mkdir(mode=0o700)
    row = run_sample(args.timer, directory / "usage.json",
                     command_for(mode, program, args.sdk / "bin64/drrun", args.client, records))
    row.update(fixture=fixture, mode=mode, phase=phase, index=index)
    error = process_error(row)
    if not error and row["stdout"] != struct.pack("<Q", FIXTURES[fixture]["output"]):
        error = "fixture output oracle failed"
    unexpected = Path(f"/proc/self/task/{os.getpid()}/children").read_text().strip()
    if not cleanup_orphans() or unexpected:
        error = "unexpected descendants or incomplete cleanup"
    if not error and mode.startswith("counter_"):
        counted = run_timed([str(args.runner_bin), "collect", "--dir", str(records),
                             "--root-pid", str(row["pid"])])
        error = process_error(counted)
        row["collector_exit_code"] = counted["exit_code"]
        row["collector_diagnostics"] = counted["stderr"].decode(errors="replace")
        try:
            row["measurement"] = json.loads(counted["stdout"])
            error = error or measurement_error(row["measurement"], fixture, mode)
        except (ValueError, UnicodeError):
            error = "malformed collector output"
    row.update(passed=error is None, reason=error)
    row["stdout_hex"] = row.pop("stdout").hex()
    row["stderr"] = row["stderr"].decode(errors="replace")
    return row


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("sdk", "client", "runner-bin", "output-dir"):
        parser.add_argument("--" + flag, type=Path, required=True)
    parser.add_argument("--warmups", type=int, choices=range(1, 4), default=1)
    parser.add_argument("--repeats", type=int, choices=range(1, 11), default=5)
    parser.add_argument("--cpu", type=int, help="allowed logical CPU; default first allowed CPU")
    args = parser.parse_args(argv)
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        parser.error("overhead fixtures require Linux x86-64")
    affinity = os.sched_getaffinity(0)
    cpu = min(affinity) if args.cpu is None else args.cpu
    if cpu not in affinity:
        parser.error("--cpu must be in the harness's allowed affinity")
    if os.path.lexists(args.output_dir):
        parser.error("--output-dir must be a fresh directory")
    args.sdk, args.client, args.runner_bin, args.output_dir = [path.resolve() for path in
        (args.sdk, args.client, args.runner_bin, args.output_dir)]
    args.output_dir.mkdir(mode=0o700, parents=True)
    summary = {"schema": 1, "experiment": "fixed_fixture_invocation_overhead",
               "authority": "diagnostic_only", "integrity_verified": False,
               "passed": False, "failure": None, "warmups": args.warmups, "repeats": args.repeats,
               "scope": "entire_native_or_drrun_invocation_excluding_collection",
               "rusage": "wait4_per_direct_child_linux_kib", "child_environment": CHILD_ENV,
               "required_isa": "linux_x86_64_baseline",
               "original_affinity": sorted(affinity), "selected_cpu": cpu,
               "fixtures": FIXTURES, "rows": [], "statistics": {}}
    libc = previous = None
    paths, executable_paths = {}, {}
    started = time.monotonic()
    try:
        os.sched_setaffinity(0, {cpu})
        libc, previous = own_subreaper()
        compiler = Path(shutil.which("gcc") or "missing-gcc").resolve(strict=True)
        paths = {"compiler": compiler, "client": args.client, "collector": args.runner_bin,
                 "drrun": args.sdk / "bin64/drrun",
                 "runtime_manifest": args.sdk / ".durante-runtime.json",
                 "python": Path(sys.executable).resolve()}
        for name in ("experiments/overhead.py", "experiments/overhead_fixture.S", "experiments/overhead_timer.c", "client/durante.c",
                     "client/CMakeLists.txt", "Cargo.toml", "Cargo.lock", "collect.py",
                     "tests/concurrent_spawn.py", "tests/fixtures.py", "toolkit/sdk.lock.json",
                     "toolkit/build_runtime.py", "toolkit/patches/dynamorio-vfork-lifetime.patch"):
            paths[name] = ROOT / name
        paths.update({str(path.relative_to(ROOT)): path for path in (ROOT / "src").glob("*.rs")})
        for directory in (args.sdk / "lib64/release", args.sdk / "ext/lib64/release"):
            paths.update({str(path.relative_to(args.sdk)): path for path in directory.glob("*.so")})
        paths["release_core"] = args.sdk / "lib64/release/libdynamorio.so"
        for name in ("as", "ld", "cc1"):
            found = tool_output([str(compiler), "-print-prog-name=" + name])
            paths["compiler_" + name] = Path(shutil.which(found) or found).resolve(strict=True)
        summary["inputs_before"] = identities(paths, cpu)
        summary["compiler_version"] = tool_output([str(compiler), "--version"])
        summary["python_version"] = sys.version
        summary["source_revision"] = tool_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"])
        summary["source_dirty"] = bool(tool_output(["git", "-C", str(ROOT), "status", "--porcelain"]))
        args.timer = args.output_dir / "overhead-timer"
        tool_output([str(compiler), "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror",
                     str(ROOT / "experiments/overhead_timer.c"), "-o", str(args.timer)])
        programs = {}
        for fixture, expected in FIXTURES.items():
            program = args.output_dir / fixture
            tool_output([str(compiler), "-static", "-nostdlib", "-no-pie", "-Wl,--build-id=none",
                         "-DFIXTURE=" + str(expected["define"]),
                         str(ROOT / "experiments/overhead_fixture.S"), "-o", str(program)])
            programs[fixture] = program
        executable_paths = {"fixture_" + name: path for name, path in programs.items()}
        executable_paths["timer"] = args.timer
        summary["executables_before"] = identities(executable_paths, cpu)
        for phase, count in (("warmup", args.warmups), ("sample", args.repeats)):
            for index in range(count):
                for offset, (fixture, program) in enumerate(programs.items()):
                    rotation = (index + offset) % len(MODES)
                    for mode in MODES[rotation:] + MODES[:rotation]:
                        if time.monotonic() - started > 180:
                            raise ValueError("experiment exceeded its 180-second campaign bound")
                        directory = args.output_dir / f"{phase}-{index}-{fixture}-{mode}"
                        row = observe(args, directory, program, fixture, mode, phase, index)
                        summary["rows"].append(row)
                        if not row["passed"]:
                            raise ValueError(f"{directory.name}: {row['reason']}")
        error = inspect_matrix(summary["rows"], args.warmups, args.repeats)
        if error:
            raise ValueError(error)
        summary["passed"] = True
    except (OSError, ValueError, RuntimeError, StopIteration) as error:
        summary["failure"] = str(error)[:1000]
    finally:
        try:
            summary["inputs_after"] = identities(paths, cpu)
            if executable_paths:
                summary["executables_after"] = identities(executable_paths, cpu)
        except (OSError, ValueError, StopIteration) as error:
            summary["failure"] = "final identity check failed: " + str(error)[:500]
        if libc is not None:
            summary["cleanup_complete"] = cleanup_orphans()
            libc.prctl(36, previous, 0, 0, 0)
        else:
            summary["cleanup_complete"] = False
        os.sched_setaffinity(0, affinity)
        finalize(summary)
        summary["elapsed_seconds"] = time.monotonic() - started
        encoded = (json.dumps(summary, indent=2, allow_nan=False) + "\n").encode()
        if len(encoded) > 8 << 20:
            raise ValueError("result exceeds 8 MiB bound")
        path = args.output_dir / "results.json"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded)
    print(json.dumps({key: summary[key] for key in ("passed", "failure", "elapsed_seconds")},
                     allow_nan=False))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
