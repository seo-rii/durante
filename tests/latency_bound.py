#!/usr/bin/env python3
"""Check the static per-block latency bound (client/latency_bound.h) exactly.

Each fixture in tests/latency_bound/kernels.S has three blocks whose bounds are
derived by hand from the model: the entry block (setup plus the first loop
iteration, no recurrence), the self-loop body executed ITERATIONS - 1 times,
and the 3-instruction exit block (issue cost 12/16 cycles). Two iteration
counts must reproduce the totals exactly under the stock-core test client.

With --client and --runner-bin, the same fixtures also run through the
production client and `durante run --latency-bound`, which must report the
same instructions and bound, and no bound without the flag. The code-replay
fixtures (tests/jit_replay.S) then check that the bound survives rollback:
the measurement stays valid, instructions do not change with the flag, and
the bound keeps at least the 4/16-cycle issue cost per instruction.

Flat and live (--work-budget) entries charge the bound inline; adding
--memory-operand-bytes keeps the block-entry and write callbacks. Every
fixture below (kernels, code replay, REP lifecycle, operand bytes) must
count exactly the same through both, on the release core through
`durante run` and, with --engine-debug, on the debug core. With
--reference-client (a client built from another revision, for example one
whose -latency_bound always used callbacks) every fixture, including the
signal-context fixtures, must also count the same as that client.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
# mode: (name, entry instructions, entry bound, body instructions, body bound)
# Bounds are sixteenths of a cycle: issue = 4 per instruction, recurrence =
# 16 * cycles per iteration (imul 2 * 3; load 4 + 1; one ALU op; xor 1 + div 20).
KERNELS = {
    0: ("multiply chain", 6, 24, 4, 96),
    1: ("pointer chase", 5, 20, 3, 80),
    2: ("independent ALU (issue bound)", 9, 36, 8, 32),
    3: ("divide chain", 8, 32, 5, 336),
    4: ("address-generation chain", 6, 24, 4, 32),
}
EXIT_INSTRUCTIONS, EXIT_BOUND = 3, 12
ITERATIONS = (1000, 2000)
REPLAY_CASES = range(6)
# rep_breakdown.S lifecycle modes (ordinary, fork, exec, failed exec, vfork),
# and whether the parent observes a signal (SIGCHLD). The thread mode spins
# until its thread starts, so its count differs from run to run.
REP_MODES = {0: False, 1: True, 2: False, 3: False, 5: True}
# A huge budget runs the live clock without stopping the fixture.
LIVE_BUDGET = "1000000000000"
# A measurement's counts, without the identities that differ per run and
# the operand bytes that only the callback variant requests.
IGNORED = {"pid", "root_pid", "parent_pid", "parent_segment", "segment", "memory_operand_bytes"}
COUNTED = ("valid", "complete", "quality", "instructions", "latency_bound", "rep_bytes",
           "rep_entries", "rep_breakdown", "unsupported_rep_entries", "signals_observed",
           "process_count", "image_count", "processes", "segments")


def expected(mode, iterations):
    _, entry_n, entry_b, body_n, body_b = KERNELS[mode]
    return {"instructions": entry_n + body_n * (iterations - 1) + EXIT_INSTRUCTIONS,
            "latency_bound_x16": entry_b + body_b * (iterations - 1) + EXIT_BOUND}


def run(command, timeout=300):
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)


def build(sdk, output):
    build_dir = output / "build"
    for command in (["cmake", "-S", str(ROOT / "tests/latency_bound"), "-B", str(build_dir),
                     "-DCMAKE_BUILD_TYPE=Release", f"-DDynamoRIO_DIR={sdk / 'cmake'}"],
                    ["cmake", "--build", str(build_dir), "-j2"]):
        result = run(command)
        if result.returncode:
            raise SystemExit(result.stdout + result.stderr)
    binaries = {}
    for mode in KERNELS:
        for iterations in ITERATIONS:
            path = output / f"kernel-{mode}-{iterations}"
            result = run(["gcc", "-static", "-nostdlib", "-no-pie", f"-DMODE={mode}",
                          f"-DITERATIONS={iterations}", str(ROOT / "tests/latency_bound/kernels.S"),
                          "-o", str(path)])
            if result.returncode or result.stderr:
                raise SystemExit(result.stdout + result.stderr)
            binaries[mode, iterations] = path
    return build_dir / "liblatency_bound_client.so", binaries


def oracle(sdk, client, binary, output, debug):
    result_path = output / (binary.name + (".debug" if debug else "") + ".json")
    command = [str(sdk / "bin64/drrun")] + (["-debug"] if debug else []) + [
        "-quiet", "-disable_traces", "-c", str(client), "-out", str(result_path), "--", str(binary)]
    process = run(command)
    if process.returncode or not result_path.exists():
        raise AssertionError(f"test client failed: {command}\n{process.stderr[-4000:]}")
    return json.loads(result_path.read_text())


def production(sdk, runner, client, binary, output, latency, operand_bytes=False):
    mode = "operand-bytes" if operand_bytes else ("latency" if latency else "plain")
    report = output / f"{binary.name}-{mode}.report.json"
    command = [str(runner), "run", "--drrun", str(sdk / "bin64/drrun"), "--client", str(client),
               "--report", str(report), "--timeout-ms", "120000"]
    if latency:
        command.append("--latency-bound")
    if operand_bytes:
        command.append("--memory-operand-bytes")
    process = run(command + ["--", str(binary)])
    if not report.exists():
        raise AssertionError(f"durante run wrote no report: {command}\n{process.stderr[-4000:]}")
    return process.returncode, json.loads(report.read_text())


def memory_operand_fixture(sdk, runner, client, output):
    binary = output / "memory-operand-bytes"
    result = run(["gcc", "-static", "-nostdlib", "-no-pie",
                  str(ROOT / "tests/latency_bound/memory_operand_bytes.S"), "-o", str(binary)])
    if result.returncode or result.stderr:
        raise AssertionError(result.stdout + result.stderr)
    if run([str(binary)]).returncode:
        raise AssertionError("native XMM sentinel changed")
    observed = {}
    for enabled in (False, True):
        code, report = production(sdk, runner, client, binary, output, True, enabled)
        m = report.get("measurement", {})
        if report["execution"]["exit_code"] != 0:
            raise AssertionError(f"instrumented XMM sentinel changed: {report['execution']}")
        if code or m.get("valid") is not True or m.get("rep_bytes") != 11:
            raise AssertionError(f"operand fixture failed: {code}, {m}")
        if enabled:
            if m.get("memory_operand_bytes") != {"version": 1, "bytes": 623}:
                raise AssertionError(f"wrong operand bytes: {m}")
        elif "memory_operand_bytes" in m:
            raise AssertionError(f"unrequested operand bytes: {m}")
        observed[enabled] = (m["instructions"], m["latency_bound"])
    if observed[True] != observed[False]:
        raise AssertionError(f"operand mode changed instruction or latency totals: {observed}")
    return {"bytes": 623, "rep_bytes": 11, "instructions": observed[True][0]}


def replay_fixtures(output):
    binaries = {}
    for case in REPLAY_CASES:
        path = output / f"jit-replay-{case}"
        result = run(["gcc", "-static", "-nostdlib", "-no-pie", f"-DCASE={case}",
                      str(ROOT / "tests/jit_replay.S"), "-o", str(path)])
        if result.returncode or result.stderr:
            raise SystemExit(result.stdout + result.stderr)
        binaries[case] = path
    return binaries


def counted(measurement):
    def strip(value):
        if isinstance(value, dict):
            return {k: strip(v) for k, v in value.items() if k not in IGNORED}
        if isinstance(value, list):
            return [strip(v) for v in value]
        return value
    return {k: strip(measurement.get(k)) for k in COUNTED}


def equality_fixtures(output, binaries, replay, operand):
    """(name, argv, expected stdout or None, signals) for the equality matrix."""
    fixtures = [(path.name, [str(path)], None, False) for (_, iterations), path in sorted(binaries.items())
                if iterations == ITERATIONS[0]]
    fixtures += [(path.name, [str(path)], None, False) for path in replay.values()]
    fixtures.append((operand.name, [str(operand)], None, False))
    for mode, signals in REP_MODES.items():
        path = output / f"rep-breakdown-{mode}"
        result = run(["gcc", "-static", "-nostdlib", "-no-pie", f"-DMODE={mode}",
                      str(ROOT / "tests/rep_breakdown.S"), "-o", str(path)])
        if result.returncode or result.stderr:
            raise SystemExit(result.stdout + result.stderr)
        fixtures.append((path.name, [str(path)], None, signals))
    # A fault handler reads registers that the fault's block made dead, and
    # a fault and an asynchronous signal interrupt blocks with live state.
    for name, source, flags, argv, stdout in (
            ("signal-context", "tests/signal_context.c", [], [], "signal-context-ok\n"),
            ("signal-state", "tests/signal_state.c", ["-mgeneral-regs-only"], ["fault", "sse2"], None)):
        path = output / name
        result = run(["gcc", "-std=c17", "-O2", "-Wall", "-Wextra", "-Werror", *flags,
                      str(ROOT / source), "-o", str(path)])
        if result.returncode or result.stderr:
            raise SystemExit(result.stdout + result.stderr)
        fixtures.append((name, [str(path), *argv], stdout, True))
    return fixtures


def measure(sdk, runner, client, argv, directory, live, callbacks, debug):
    """One run of the production client with -latency_bound. Returns the
    measurement, the exit status and stdout of the measured program."""
    directory.mkdir(mode=0o700)
    if not debug:
        report = directory / "report.json"
        command = [str(runner), "run", "--drrun", str(sdk / "bin64/drrun"), "--client", str(client),
                   "--report", str(report), "--timeout-ms", "120000", "--latency-bound"]
        if callbacks:
            command.append("--memory-operand-bytes")
        if live:
            command += ["--work-budget", LIVE_BUDGET]
        process = run(command + ["--", *argv])
        if not report.exists():
            raise AssertionError(f"durante run wrote no report: {command}\n{process.stderr[-4000:]}")
        envelope = json.loads(report.read_text())
        return envelope.get("measurement", {}), envelope["execution"].get("exit_code"), process.stdout
    records = directory / "records"
    records.mkdir(mode=0o700)
    command = [str(sdk / "bin64/drrun"), "-debug", "-quiet", "-stderr_mask", "12", "-follow_children",
               "-disable_traces", "-vm_size", "32M", "-vmheap_size", "32M", "-c", str(client),
               "-outdir", str(records), "-rep_bytes"]
    if live:
        page = directory / "live"
        page.write_bytes(bytes(48))
        command += ["-live_work_v1", str(page)]
    command.append("-latency_bound")
    if callbacks:
        command.append("-memory_operand_bytes")
    process = subprocess.Popen(command + ["--", *argv], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, cwd=directory)
    stdout, stderr = process.communicate(timeout=300)
    collected = run([str(runner), "collect", "--dir", str(records), "--root-pid", str(process.pid)])
    try:
        measurement = json.loads(collected.stdout)
    except ValueError:
        raise AssertionError(f"debug-core records not collected: {command}\n{stderr[-4000:]}") from None
    return measurement, process.returncode, stdout


def equality(sdk, runner, client, reference, fixtures, output, debug):
    """Inline entries must count exactly what the entry callbacks count."""
    rows, failures = [], []
    engine = "debug" if debug else "release"
    for name, argv, stdout, signals in fixtures:
        for live in (False, True):
            label = f"{engine}-{'live' if live else 'flat'}-{name}"
            variants = {"inline": (client, False)}
            if not signals:  # operand bytes invalidate a run that observes a signal
                variants["callbacks"] = (client, True)
            if reference:
                variants["reference"] = (reference, False)
            results = {variant: measure(sdk, runner, path, argv, output / f"{label}-{variant}", live,
                                        callbacks, debug)
                       for variant, (path, callbacks) in variants.items()}
            inline, code, out = results["inline"]
            row = {"fixture": name, "engine": engine, "live": live,
                   "instructions": inline.get("instructions"), "latency_bound": inline.get("latency_bound"),
                   "compared": sorted(set(variants) - {"inline"})}
            rows.append(row)
            if inline.get("valid") is not True or code != 0 or (stdout is not None and out != stdout):
                failures.append(f"{label}: invalid inline measurement or fixture failure (exit {code})")
            if signals and not inline.get("signals_observed"):
                failures.append(f"{label}: the fixture observed no signal")
            for variant, (measurement, other_code, other_out) in results.items():
                if variant == "inline":
                    continue
                if counted(measurement) != counted(inline) or other_code != code or other_out != out:
                    failures.append(f"{label}: {variant} differs: {counted(measurement)} != {counted(inline)}")
    return rows, failures


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sdk", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--engine-debug", action="store_true", help="also run the oracle on the debug core")
    parser.add_argument("--client", type=Path, help="production libdurante.so")
    parser.add_argument("--runner-bin", type=Path, help="durante CLI")
    parser.add_argument("--reference-client", type=Path,
                        help="another libdurante.so whose counts every fixture must also match")
    args = parser.parse_args()
    if (args.client is None) != (args.runner_bin is None):
        parser.error("--client and --runner-bin must be supplied together")
    if args.reference_client and not args.client:
        parser.error("--reference-client requires --client and --runner-bin")
    args.output_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    output = args.output_dir.resolve()
    sdk = args.sdk.resolve()
    test_client, binaries = build(sdk, output)
    failures, rows = [], []
    for (mode, iterations), binary in sorted(binaries.items()):
        want = expected(mode, iterations)
        row = {"mode": mode, "kernel": KERNELS[mode][0], "iterations": iterations, "expected": want}
        for debug in (False, True) if args.engine_debug else (False,):
            got = oracle(sdk, test_client, binary, output, debug)
            label = "oracle-debug" if debug else "oracle"
            row[label] = got
            if got.get("unanalyzable") != 0 or {k: got.get(k) for k in want} != want:
                failures.append(f"{label} {KERNELS[mode][0]} x{iterations}: {got} != {want}")
        if args.client:
            for latency in (True, False):
                code, report = production(sdk, args.runner_bin.resolve(), args.client.resolve(),
                                          binary, output, latency)
                measurement = report.get("measurement", {})
                label = "production" + ("" if latency else "-without-flag")
                row[label] = {k: measurement.get(k) for k in ("valid", "instructions", "latency_bound")}
                bound = measurement.get("latency_bound")
                ok = (code == 0 and measurement.get("valid") is True
                      and measurement.get("instructions") == want["instructions"])
                if latency:
                    ok = ok and report.get("latency_bound_version") == 1 and bound == {
                        "version": 1, "cycles_x16": want["latency_bound_x16"]}
                else:
                    ok = ok and bound is None and "latency_bound_version" not in report
                if not ok:
                    failures.append(f"{label} {KERNELS[mode][0]} x{iterations}: {row[label]} (exit {code})")
        rows.append(row)
    replay = []
    replay_binaries = replay_fixtures(output) if args.client else {}
    if args.client:
        for case, binary in replay_binaries.items():
            results = {latency: production(sdk, args.runner_bin.resolve(), args.client.resolve(),
                                           binary, output, latency) for latency in (True, False)}
            (code_on, on), (code_off, off) = results[True], results[False]
            m_on, m_off = on.get("measurement", {}), off.get("measurement", {})
            bound = (m_on.get("latency_bound") or {}).get("cycles_x16")
            entry = {"case": case, "instructions": m_on.get("instructions"),
                     "instructions_without_flag": m_off.get("instructions"), "latency_bound_x16": bound}
            replay.append(entry)
            if not (code_on == code_off == 0 and m_on.get("valid") is True and m_off.get("valid") is True
                    and m_on.get("instructions") == m_off.get("instructions")
                    and type(bound) is int and bound >= 4 * m_on["instructions"]):
                failures.append(f"replay case {case}: {entry}")
    summary = {"kernels": rows, "replay": replay, "failures": failures}
    if args.client:
        summary["memory_operand_bytes"] = memory_operand_fixture(
            sdk, args.runner_bin.resolve(), args.client.resolve(), output)
        fixtures = equality_fixtures(output, binaries, replay_binaries, output / "memory-operand-bytes")
        reference = args.reference_client.resolve() if args.reference_client else None
        summary["equality"] = []
        for debug in (False, True) if args.engine_debug else (False,):
            equal_rows, equal_failures = equality(sdk, args.runner_bin.resolve(), args.client.resolve(),
                                                  reference, fixtures, output, debug)
            summary["equality"] += equal_rows
            failures += equal_failures
    summary["passed"] = not failures
    (output / "result.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
