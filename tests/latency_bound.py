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


def production(sdk, runner, client, binary, output, latency):
    report = output / (binary.name + ("-latency" if latency else "-plain") + ".report.json")
    command = [str(runner), "run", "--drrun", str(sdk / "bin64/drrun"), "--client", str(client),
               "--report", str(report), "--timeout-ms", "120000"]
    if latency:
        command.append("--latency-bound")
    process = run(command + ["--", str(binary)])
    if not report.exists():
        raise AssertionError(f"durante run wrote no report: {command}\n{process.stderr[-4000:]}")
    return process.returncode, json.loads(report.read_text())


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


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sdk", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--engine-debug", action="store_true", help="also run the oracle on the debug core")
    parser.add_argument("--client", type=Path, help="production libdurante.so")
    parser.add_argument("--runner-bin", type=Path, help="durante CLI")
    args = parser.parse_args()
    if (args.client is None) != (args.runner_bin is None):
        parser.error("--client and --runner-bin must be supplied together")
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
    if args.client:
        for case, binary in replay_fixtures(output).items():
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
    summary = {"kernels": rows, "replay": replay, "failures": failures, "passed": not failures}
    (output / "result.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
