#!/usr/bin/env python3
"""Bounded BUG-006 recurrence; failed observations are never retried to green."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import signal
import subprocess
import sys
import time
from types import SimpleNamespace

from concurrent_spawn import bounded_number, cleanup_orphans, own_subreaper
from java_tiered import check_production_runtime
from runtime_compat import InputProvenance, bounded_bytes, executable_path, run_process
import runtime_workloads as workloads

ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN_SECONDS = 600
WALL_MS = 120000
COLLECTION_ALLOWANCE_MS = 10000
TERMINATION_CLEANUP_MS = 8000
MAX_FATAL_BYTES = 1048576
MAX_FATAL_FILES = 16
SIGNATURE = "guarantee(cb != NULL && cb->is_compiled()) failed: safepoint polling: pc must refer to an nmethod"


def fatal_evidence(data, path):
    """Keep the fatal-handler snapshot separate from the missing original context."""
    text = data.decode("utf-8", errors="replace")
    header = re.search(r"Internal Error \((sharedRuntime\.cpp:[0-9]+)\), pid=([0-9]+), tid=([0-9]+)", text)
    identity = re.search(r"\bpid=([0-9]+), tid=([0-9]+)", text)
    frames = text.split("Native frames:", 1)[-1].split("Registers:", 1)[0] if "Native frames:" in text else ""
    safepoint = bool(header and SIGNATURE in text and "SharedRuntime::get_poll_stub(" in frames)
    registers = {}
    if "Registers:" in text:
        block = text.split("Registers:", 1)[1].split("XMM[", 1)[0][:8192]
        registers = {name: value.lower() for name, value in re.findall(r"\b(RIP|R12)=\s*(0x[0-9a-fA-F]+)", block)}
    return {"path": str(path), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
            "symptom": "safepoint_pc_lookup_guarantee" if safepoint else "other_jvm_fatal",
            "pid": int(identity[1]) if identity else None,
            "tid": int(identity[2]) if identity else None,
            "source_location": header[1] if header else None,
            "stage": "jvm_bootstrap" if "java.lang.System.initPhase2(" in frames else "unestablished",
            "fatal_register_snapshot": registers,
            "original_polling_pc": None, "original_signal_context": None,
            "context_evidence": "hs_err fatal registers do not establish the original polling PC or translation result"}


def inspect_fatal_reports(directory):
    reports = []
    paths = sorted(directory.glob("hs_err_pid*.log"))
    if len(paths) > MAX_FATAL_FILES:
        raise ValueError("fatal report count exceeds inspection bound")
    for path in paths:
        reports.append(fatal_evidence(bounded_bytes(path, MAX_FATAL_BYTES), path))
    return reports


def classify_failure(row):
    if row.get("timeout") or row.get("report", {}).get("execution", {}).get("state") == "timed_out":
        return "timeout"
    if not row.get("cleanup_complete"):
        return "cleanup_incomplete"
    reports = row.get("fatal_reports", [])
    root = row.get("report", {}).get("execution", {}).get("root_pid", row.get("root_pid"))
    matching = [r for r in reports if r["symptom"] == "safepoint_pc_lookup_guarantee"]
    if matching:
        return "root_safepoint_pc_guarantee" if any(r["pid"] == root for r in matching) else "nonroot_safepoint_pc_guarantee"
    if reports:
        return "other_jvm_fatal"
    if isinstance(row.get("exit_code"), int) and row["exit_code"] < 0:
        return "other_root_signal"
    return "none" if row.get("passed") else "other_failure"


def observation_plan(args, workload):
    modes = ("native", "engine-only-gdb") if args.context else workloads.MODES
    return [{"repetition": repeat, "mode": mode, "state": "incomplete", "passed": False,
             "directory": str(args.output_dir / f"{repeat:03d}" / (workload.name + "-" + mode)),
             "errors": [], "fatal_reports": [], "failure_class": "not_run"}
            for repeat in range(1, args.repeats + 1) for mode in modes]


def inspect_context(path):
    data = bounded_bytes(path, 262144)
    events = [json.loads(line) for line in data.splitlines()]
    if not events or len(events) > 260 or any(not isinstance(e, dict) or e.get("schema") != 1 for e in events):
        raise ValueError("invalid bounded debugger context stream")
    kinds = {event.get("event") for event in events}
    errors = [event for event in events if event.get("event") in ("error", "truncated")]
    return {"path": str(path), "sha256": hashlib.sha256(data).hexdigest(), "events": len(events),
            "signal_stop_observed": "signal_stop" in kinds,
            "hotspot_context_observed": "hotspot_signal_context" in kinds,
            "poll_stub_argument_observed": "poll_stub_argument" in kinds,
            "inferior_exit_observed": "inferior_exit" in kinds,
            "errors": errors, "complete": not errors and all(k in kinds for k in
                ("signal_stop", "hotspot_signal_context", "poll_stub_argument", "inferior_exit")),
            "interpretation": "debugger signal-stop PC, delivered HotSpot ucontext, and poll-stub argument are distinct observations; no causal translation claim"}


def debugger_command(debugger, engine):
    # Configure the CLI interpreter before entering Python. On GDB 13.1,
    # enabling logging inside the Python script redirects ordinary messages but
    # leaves asynchronous run/continue stop frames on the original stdout.
    return [debugger, "--nx", "--nh", "--batch", "--return-child-result",
            "-ex", "set logging file debugger-transcript.log",
            "-ex", "set logging redirect on", "-ex", "set logging enabled on",
            "-x", str(ROOT / "tests/jvm_safepoint_gdb.py"), "--args", *engine]


def run_context(args, workload, directory, timeout):
    directory.mkdir(mode=0o700)
    engine = [str(args.drrun), "-quiet", "-follow_children", "-disable_traces", "-vm_size", "32M",
              "-vmheap_size", "32M", "-stderr_mask", "12", "--", *workload.command]
    command = debugger_command(args.gdb, engine)
    row = run_process(command, directory, timeout, args.prlimit, cpu_seconds=90)
    # Redirect debugger chatter to its own bounded transcript. Debugger startup
    # may still share stdout, so the exact target stdout oracle remains in the
    # separate, unmodified recurrence matrix.
    row.update(command=command, diagnostic_only=True)
    try:
        transcript = directory / "debugger-transcript.log"
        data = bounded_bytes(transcript, MAX_FATAL_BYTES)
        row["debugger_transcript"] = {"path": str(transcript), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        row["context"] = inspect_context(directory / "jvm-context.jsonl")
        if row["context"]["errors"] or not row["context"]["inferior_exit_observed"]:
            row["errors"].append("debugger capture failed, truncated, or has no inferior exit")
    except (OSError, ValueError, TypeError) as error:
        row["errors"].append("context capture unavailable: " + str(error)[:300])
    row["passed"] = row["infrastructure_ok"] and row["exit_code"] == 0 and not row["errors"]
    return row


def run_observations(args, workload, rows, deadline):
    blocked = None
    for row in rows:
        remaining = int((deadline - time.monotonic()) * 1000)
        allowance = 5000 if row["mode"] == "supervisor" else 0
        required = WALL_MS + allowance + COLLECTION_ALLOWANCE_MS + TERMINATION_CLEANUP_MS
        if blocked is None and remaining < required:
            blocked = "campaign budget cannot fit the full observation and fixed collection/cleanup margins"
        if blocked:
            row["errors"].append(blocked)
            continue
        directory = Path(row["directory"])
        local = SimpleNamespace(**vars(args))
        local.output_dir = directory.parent
        local.timeout_ms = WALL_MS
        try:
            directory.parent.mkdir(mode=0o700, exist_ok=True)
            observed = (run_context(local, workload, directory, local.timeout_ms) if row["mode"] == "engine-only-gdb"
                        else workloads.run_case(local, workload, row["mode"]))
            row.update(observed, state="complete")
            row["fatal_reports"] = inspect_fatal_reports(directory)
            if row["fatal_reports"]:
                row["errors"].append("JVM fatal-error report present")
                row["passed"] = False
            row["failure_class"] = classify_failure(row)
            if not row["cleanup_complete"]:
                blocked = "prior observation descendant cleanup incomplete"
            elif row["mode"] == "native" and not row["passed"]:
                blocked = "native control failed"
        except (OSError, ValueError, RuntimeError, KeyError, TypeError, subprocess.SubprocessError) as error:
            row["passed"] = False
            row["failure_class"] = "inspection_or_execution_failure"
            row["errors"].append(f"observation failed: {type(error).__name__}: {error}"[:600])
            blocked = "prior observation could not complete"


def plan_errors(args, rows):
    modes = ("native", "engine-only-gdb") if args.context else workloads.MODES
    expected = [(r, m) for r in range(1, args.repeats + 1) for m in modes]
    if [(r["repetition"], r["mode"]) for r in rows] != expected:
        return ["fixed observation plan is missing, duplicated or reordered"]
    return [f"repetition {r['repetition']} {r['mode']}: {r['state']} failed"
            for r in rows if r["state"] != "complete" or not r["passed"]]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("sdk", "client", "runner-bin", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--java", required=True)
    parser.add_argument("--javac", required=True)
    parser.add_argument("--java-major", type=int, choices=(17, 21), default=17)
    parser.add_argument("--repeats", type=bounded_number(1, 5))
    parser.add_argument("--context", action="store_true", help="separate one-shot GDB diagnostic; never runtime qualification")
    parser.add_argument("--gdb", default="gdb")
    args = parser.parse_args(argv)
    args.repeats = args.repeats or (1 if args.context else 5)
    if args.context and args.repeats != 1:
        parser.error("context capture is a separate one-shot diagnostic")
    for name in ("sdk", "client", "runner_bin", "output_dir"):
        setattr(args, name, getattr(args, name).absolute())
    try:
        args.output_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    except OSError:
        parser.error("output directory must be fresh and writable")
    args.java, args.javac = executable_path(args.java), executable_path(args.javac)
    args.prlimit = executable_path("prlimit")
    args.gdb = executable_path(args.gdb) if args.context else None
    args.drrun, args.collector_bin = args.sdk / "bin64/drrun", args.runner_bin
    started = time.monotonic()
    deadline = started + CAMPAIGN_SECONDS
    home = Path(args.java).resolve().parent.parent if args.java else None
    sources = ("tests/jvm_safepoint.py", "tests/jvm_safepoint_gdb.py", "tests/java_tiered.py",
               "tests/runtime_workloads.py", "tests/runtime_compat.py", "tests/concurrent_spawn.py",
               "tests/fixtures.py", "collect.py", "tests/runtime_workload_fixtures/RuntimeWorkload.java",
               "toolkit/build_runtime.py", "toolkit/patches/dynamorio-vfork-lifetime.patch",
               "toolkit/patches/dynamorio-code-replay.patch")
    inputs = {"source:" + name: ROOT / name for name in sources}
    inputs.update({"binary:java": args.java, "binary:javac": args.javac,
                   "binary:drrun": args.drrun, "binary:core": args.sdk / "lib64/release/libdynamorio.so",
                   "binary:client": args.client, "binary:runner": args.runner_bin,
                   "binary:prlimit": args.prlimit, "binary:harness-python": Path(sys.executable).resolve(),
                   "runtime:manifest": args.sdk / ".durante-runtime.json"})
    for name in ("lib/server/libjvm.so", "lib/modules", "lib/jspawnhelper", "release"):
        inputs["jdk:" + name] = home / name if home else None
    if args.context:
        inputs["binary:gdb"] = args.gdb
    provenance = InputProvenance(inputs)
    provenance.capture("before")
    workload = next(w for w in workloads.workload_cases({"java": args.java or "<missing-java>"}, args.output_dir / "classes")
                    if w.name == "java-processes-posix_spawn")
    rows = observation_plan(args, workload)
    failures, versions, owner, previous_affinity = [], {}, None, None
    cleaned = True
    affinity = {"original": None, "selected": None, "restored": True}
    previous_handlers = {}

    def interrupted(signum, _frame):
        raise KeyboardInterrupt("signal " + str(signum))

    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous_handlers[signum] = signal.signal(signum, interrupted)
        if provenance.errors:
            raise ValueError("required runtime or harness input unavailable")
        if Path(args.javac).resolve().parent.parent != home:
            raise ValueError("java and javac must belong to the same JDK")
        check_production_runtime(args, provenance)
        owner = own_subreaper()
        previous_affinity = os.sched_getaffinity(0)
        affinity.update(original=sorted(previous_affinity), selected=sorted(previous_affinity)[:2], restored=False)
        os.sched_setaffinity(0, affinity["selected"])
        versions, _ = workloads.probe_and_compile(args, {"java": args.java}, provenance)
        run_observations(args, workload, rows, deadline)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        failures.append(f"harness failure: {type(error).__name__}: {error}"[:600])
    finally:
        if owner is not None:
            try:
                cleaned = cleanup_orphans()
                if owner[0].prctl(36, owner[1], 0, 0, 0):
                    cleaned = False
            except OSError as error:
                cleaned = False
                failures.append("cleanup inspection failed: " + str(error)[:300])
        if previous_affinity is not None:
            try:
                os.sched_setaffinity(0, previous_affinity)
                affinity["restored"] = os.sched_getaffinity(0) == previous_affinity
            except OSError as error:
                failures.append("CPU affinity restoration failed: " + str(error)[:300])
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
        provenance.capture("after")
    for row in rows:
        if row["state"] == "incomplete" and not row["errors"]:
            row["errors"].append("setup did not complete")
    failures.extend(plan_errors(args, rows))
    if not provenance.unchanged():
        failures.append("qualification inputs changed or could not be inspected")
    if not cleaned or not affinity["restored"]:
        failures.append("cleanup or CPU affinity restoration incomplete")
    summary = {"schema": 1, "issue": "BUG-006", "authority": "diagnostic_only",
               "passed": not failures, "exit_code": int(bool(failures)),
               "experiment": "debugger-context" if args.context else "fixed-recurrence",
               "runtime_qualification": False, "repeats": args.repeats,
               "planned_observations": len(rows), "completed_observations": sum(r["state"] == "complete" for r in rows),
               "checks_passed": sum(r["passed"] for r in rows), "rows": rows, "versions": versions,
               "host": platform.platform(), "machine": platform.machine(), "cpu_affinity": affinity,
               "bounds": {"campaign_seconds": CAMPAIGN_SECONDS, "wall_ms": WALL_MS,
                          "supervisor_allowance_ms": 5000, "cpu_seconds_per_process": 90,
                          "collection_inspection_allowance_ms": COLLECTION_ALLOWANCE_MS,
                          "address_space_bytes_per_process": 2147483648, "file_bytes": 1048576,
                          "core_bytes": 0, "termination_cleanup_allowance_s": TERMINATION_CLEANUP_MS // 1000},
               "elapsed_s": round(time.monotonic() - started, 4), "cleanup_complete": cleaned,
               "inputs_unchanged": provenance.unchanged(), "input_provenance": {"files": provenance.files, "errors": provenance.errors},
               "failures": failures,
               "limitations": ["no retry-to-green; a successful campaign is non-reproduction, not a runtime correction",
                               "the retained CI failure happened during JVM bootstrap, before workload child creation",
                               "fatal-handler RIP/R12 do not establish the original polling PC or a translation failure",
                               "debugger observations change execution and are separate from unchanged runtime controls",
                               "runtime provider, host and selected CPU placement may differ from the original CI observation"]}
    try:
        (args.output_dir / "results.json").write_text(json.dumps(summary, indent=2) + "\n")
    except OSError as error:
        summary["passed"], summary["exit_code"] = False, 1
        failures.append("cannot publish results.json: " + str(error)[:300])
    print(json.dumps({k: v for k, v in summary.items() if k not in ("rows", "input_provenance")}))
    return summary["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
