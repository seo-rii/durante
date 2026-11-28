#!/usr/bin/env python3
"""Collect counters and native CPU times for the capacity-miss study.

Builds every case of plan.json, checks its output against an independent oracle,
runs the cache probe twice per case and times the native program in interleaved
rounds. The design is in predeclared.txt. Writes results.json into a new output
directory.

  python3 -B experiments/capacity_misses/collect.py --sdk SDK \\
      --client build/l1d/libdurante_l1d.so --output-dir NEW_DIR
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import re
import resource
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "tests/contest"))
import problems  # noqa: E402

CFLAGS = ["-O2", "-static", "-no-pie", "-march=x86-64", "-mtune=generic", "-Wl,--build-id=none"]
PROBE_L1D = re.compile(rb"^PROBE_L1D refs=(\d+) lines=(\d+) misses=(\d+) skipped=(\d+) rep=(\d+) threads=1$", re.M)
PROBE_OUTER = re.compile(rb"^PROBE_OUTER capacities=(\d+),(\d+),(\d+) accesses=(\d+) "
                         rb"misses=(\d+),(\d+),(\d+) stream=(\d+),(\d+),(\d+)"
                         rb"(?: dependent=(\d+),(\d+),(\d+) l1d_dependent=(\d+) "
                         rb"dependent_sites=(\d+) load_sites=(\d+))?$", re.M)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def unlimited_stack():
    hard = resource.getrlimit(resource.RLIMIT_STACK)[1]
    resource.setrlimit(resource.RLIMIT_STACK, (hard, hard))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def run(argv, stdin_path, cpu, timeout):
    """Run argv pinned and without ASLR; return (cpu seconds, wall, stdout, stderr, status)."""
    full = ["taskset", "-c", str(cpu), "setarch", "x86_64", "-R", *argv]
    with open(stdin_path, "rb") as stdin:
        started = time.monotonic()
        proc = subprocess.Popen(full, stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                preexec_fn=unlimited_stack)
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            raise RuntimeError(f"timeout: {argv}")
        wall = time.monotonic() - started
    # communicate() reaps the child; its rusage is the only child's usage.
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    return usage, wall, out, err, proc.returncode


class ChildTimer:
    """RUSAGE_CHILDREN accumulates, so take differences across runs."""

    def __init__(self):
        self.last = resource.getrusage(resource.RUSAGE_CHILDREN)

    def cpu(self, usage):
        cpu = (usage.ru_utime - self.last.ru_utime) + (usage.ru_stime - self.last.ru_stime)
        self.last = usage
        return cpu


def build(plan, out):
    bins = out / "bin"
    bins.mkdir()
    built = {}
    for name, spec in plan["sources"].items():
        source = ROOT / spec["path"]
        target = bins / name
        compiler = ["gcc", "-std=c17"] if source.suffix == ".c" else ["g++", "-std=c++20"]
        subprocess.run([*compiler, *CFLAGS, str(source), "-o", str(target)], check=True, timeout=300)
        built[name] = {"path": str(target), "source_sha256": sha256(source), "elf_sha256": sha256(target)}
    return built


def prepare_inputs(plan, out, built):
    inputs = out / "inputs"
    inputs.mkdir()
    cases = {}
    generated = {}
    for name, spec in plan["cases"].items():
        stdin = inputs / f"{name}.in"
        expected = None
        if "corpus" in spec:
            directory = ROOT / spec["corpus"]
            stdin.write_bytes((directory / "in").read_bytes())
            expected = (directory / "oracle").read_bytes()
        elif "problem" in spec:
            if spec["problem"] not in generated:
                data = problems.generate(spec["problem"], "full")
                generated[spec["problem"]] = (data, problems.reference(spec["problem"], data).encode())
            data, expected = generated[spec["problem"]]
            stdin.write_bytes(data)
        else:
            stdin.write_bytes(b"")
        cases[name] = {"role": spec["role"], "binary": spec["binary"], "args": spec.get("args", []),
                       "meta": spec.get("meta"),
                       "stdin": str(stdin), "stdin_sha256": sha256(stdin), "expected": expected}
    return cases


def probe(args, case, built, out, name, attempt, timer):
    records = out / "records" / f"{name}-{attempt}"
    records.mkdir(parents=True)
    argv = [str(args.drrun), "-quiet", "-disable_traces", "-c", str(args.client), "-outdir", str(records),
            "-rep_bytes", *(["-latency_bound"] if args.latency_bound else []), "-probe_l1d", "-probe_outer",
            "--", built[case["binary"]]["path"], *case["args"]]
    usage, wall, out_bytes, err, status = run(argv, case["stdin"], args.cpu, args.probe_timeout)
    cpu = timer.cpu(usage)
    if status != 0 or out_bytes != case["expected"]:
        raise RuntimeError(f"{name}: probe run failed (status {status}): {err[-500:]!r}")
    l1d, outer = PROBE_L1D.search(err), PROBE_OUTER.search(err)
    if not l1d or not outer:
        raise RuntimeError(f"{name}: probe counters missing: {err[-500:]!r}")
    ends = []
    for path in records.glob("process-*.jsonl"):
        for line in path.read_text().splitlines():
            record = json.loads(line)
            if record.get("event") == "end":
                ends.append(record)
    if len(ends) != 1 or not ends[0].get("valid") or ends[0].get("quality") != "uninterrupted":
        raise RuntimeError(f"{name}: expected one valid uninterrupted process, got {ends}")
    end = ends[0]
    refs, lines, misses, skipped, rep = map(int, l1d.groups())
    o = [int(v) if v is not None else None for v in outer.groups()]
    extra = {}
    if o[10] is not None:
        extra = {"outer_dependent": o[10:13], "l1d_dependent": o[13], "dependent_sites": o[14],
                 "load_sites": o[15]}
    if args.latency_bound:
        extra["latency_bound_x16"] = end["latency_bound_x16"]
    return {**extra, "instructions": end["instructions"], "movs_bytes": end["rep_movs_bytes"],
            "stos_bytes": end["rep_stos_bytes"], "l1d_refs": refs, "l1d_lines": lines,
            "l1d_misses": misses, "l1d_skipped": skipped, "rep_instructions": rep,
            "outer_capacities": o[0:3], "outer_accesses": o[3], "outer_misses": o[4:7],
            "outer_stream": o[7:10], "probe_cpu_s": round(cpu, 4), "probe_wall_s": round(wall, 4)}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for name in ("sdk", "client", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--plan", type=Path, default=HERE / "plan.json")
    parser.add_argument("--predeclared", type=Path, default=HERE / "predeclared.txt")
    parser.add_argument("--latency-bound", action="store_true", help="also collect the static latency bound")
    parser.add_argument("--cpu", type=int, default=1)
    parser.add_argument("--rounds", type=int, default=9)
    parser.add_argument("--seed", type=int, default=20261128)
    parser.add_argument("--probe-timeout", type=float, default=900)
    parser.add_argument("--probe-runs", type=int, default=2)
    args = parser.parse_args()
    args.drrun = (args.sdk / "bin64/drrun").resolve()
    args.client = args.client.resolve()
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=False)
    plan = json.loads(args.plan.read_text())
    built = build(plan, out)
    cases = prepare_inputs(plan, out, built)
    timer = ChildTimer()
    # Kernels have no external oracle: their first native output is the expectation,
    # and every later run must reproduce it.
    for name, case in cases.items():
        usage, _, stdout, err, status = run([built[case["binary"]]["path"], *case["args"]], case["stdin"],
                                            args.cpu, 600)
        timer.cpu(usage)
        if status != 0:
            raise RuntimeError(f"{name}: native exit {status}: {err[-300:]!r}")
        if case["expected"] is None:
            case["expected"] = stdout
        elif stdout != case["expected"]:
            raise RuntimeError(f"{name}: native output differs from the oracle")
    print("outputs verified", flush=True)
    for name, case in cases.items():
        case["probe"] = [probe(args, case, built, out, name, attempt, timer)
                         for attempt in range(1, args.probe_runs + 1)]
        keys = ("instructions", "movs_bytes", "stos_bytes", "l1d_misses", "outer_misses", "outer_stream",
                "outer_dependent", "l1d_dependent", "latency_bound_x16")
        case["probe_repeatable"] = all(run.get(k) == case["probe"][0].get(k) for run in case["probe"] for k in keys)
        print(f"probe {name}: repeatable={case['probe_repeatable']} cpu={case['probe'][0]['probe_cpu_s']}s",
              flush=True)
    order = list(cases)
    rng = random.Random(args.seed)
    for round_index in range(args.rounds):
        rng.shuffle(order)
        for name in order:
            case = cases[name]
            usage, wall, stdout, _, status = run([built[case["binary"]]["path"], *case["args"]], case["stdin"],
                                                 args.cpu, 600)
            cpu = timer.cpu(usage)
            if status != 0 or stdout != case["expected"]:
                raise RuntimeError(f"{name}: native run {round_index} failed")
            case.setdefault("native_cpu_s", []).append(round(cpu, 6))
        print(f"round {round_index + 1}/{args.rounds} done", flush=True)
    for case in cases.values():
        case["expected_sha256"] = hashlib.sha256(case.pop("expected")).hexdigest()
    host = {"cpu_model": next((l.split(":", 1)[1].strip() for l in Path("/proc/cpuinfo").read_text().splitlines()
                               if l.startswith("model name")), None),
            "kernel": os.uname().release, "cpu": args.cpu}
    result = {"schema": 1, "plan": str(args.plan.name), "plan_sha256": sha256(args.plan),
              "predeclared_sha256": sha256(args.predeclared), "rounds": args.rounds,
              "latency_bound": args.latency_bound,
              "client_sha256": sha256(args.client), "drrun_sha256": sha256(args.drrun),
              "host": host, "built": built, "cases": cases}
    (out / "results.json").write_text(json.dumps(result, indent=1) + "\n")
    print(f"wrote {out / 'results.json'}")


if __name__ == "__main__":
    main()
