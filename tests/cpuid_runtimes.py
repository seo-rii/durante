#!/usr/bin/env python3
"""Exploratory runtime checks for fixed CPUID profiles.

Runs one fixed CPython, Node and Java workload natively and under the
stock-core CPUID test client (tests/cpuid_profile), with ASLR disabled, and
records for each group: exit status, output agreement with a native run of
the same options, instruction counts and their exact relative spread.

Groups (each repeated --repeats times, each run writing a fresh result file):
- the default runtime under `none` (host CPUID) and the selected profile;
- runtime determinism options (CPython hash seed 0 with -s -P instead of -I,
  Node --predictable, Java -Xbatch with Serial GC and one active processor),
  which fix the hash seed and JIT/GC thread scheduling;
- an "above profile" mask that removes only features the profile does not
  report (AVX, AVX2, AVX-512, BMI, LZCNT, FMA, SHA, ERMS, FSRM), simulating a
  host that lacks them, through GLIBC_TUNABLES and OPENSSL_ia32cap.

This is a fixed-workload observation, not runtime qualification: it uses the
test client (no Durante lifecycle accounting), one workload per runtime, and
one host. Runtime-specific CPU flags (V8 --no-enable-*, HotSpot -XX:UseAVX)
are deliberately not used for the mask: changing V8 flags alone added a fixed
amount of work under every profile, which is not a CPU dispatch effect.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent.parent
HERE = ROOT / "tests" / "cpuid_runtimes"
GLIBC_MASK = ("glibc.cpu.hwcaps=-AVX512F,-AVX512VL,-AVX512BW,-AVX512DQ,-AVX2,-AVX,-ERMS,-FSRM,"
              "-BMI1,-BMI2,-LZCNT,-MOVBE,-FMA")
# OpenSSL capability words: CPUID.1 EDX|ECX<<32, then leaf 7 EBX|ECX<<32.
# Clears AVX (word 0 bit 60) and AVX2, BMI1/2, AVX-512F, ADX, SHA (word 1).
OPENSSL_MASK = "~0x1000000000000000:~0x20090128"
# Default and deterministic options per runtime, with deterministic environment.
# CPython's -I implies -E, which ignores PYTHONHASHSEED, so the deterministic
# group replaces it with its other parts (-s -P) and pins the hash seed.
DEFAULT = {"cpython": ["-I"], "node": [], "java": []}
DETERMINISM = {"cpython": ["-s", "-P"], "node": ["--predictable"],
               "java": ["-Xbatch", "-XX:+UseSerialGC", "-XX:ActiveProcessorCount=1"]}
DETERMINISM_ENV = {"cpython": {"PYTHONHASHSEED": "0"}}


def commands(args, workdir):
    return {
        "cpython": ([args.python], [str(HERE / "work.py"), "2"]),
        "node": ([args.node], [str(HERE / "work.js"), "200000"]),
        "java": ([args.java, "-Xshare:auto", "-XX:-UsePerfData"], ["-cp", str(workdir), "Work", "200000"]),
    }


def run(args, workloads, output, runtime, profile, options=(), env=None):
    pre, post = workloads[runtime]
    command = pre + list(options) + post
    fd, result = tempfile.mkstemp(dir=output, prefix="count-", suffix=".json")
    os.close(fd)
    os.unlink(result)
    if profile != "native":
        drrun = [str(args.sdk / "bin64/drrun")] + (["-debug"] if args.engine_debug else [])
        command = drrun + ["-quiet", "-disable_traces", "-c", str(args.client), "-profile", profile,
                           "-out", result, "--"] + command
    # No inherited PYTHON* variable applies: -I ignores them, and the
    # deterministic CPython group sets only PYTHONHASHSEED.
    full_env = {k: v for k, v in os.environ.items()
                if k not in ("JAVA_TOOL_OPTIONS", "GLIBC_TUNABLES", "OPENSSL_ia32cap") and not k.startswith("PYTHON")}
    full_env.update(env or {})
    started = time.monotonic()
    process = subprocess.run(["setarch", "-R"] + command, env=full_env, capture_output=True,
                             text=True, timeout=args.timeout)
    count = json.loads(Path(result).read_text())["instructions"] if Path(result).exists() else None
    return {"exit": process.returncode, "stdout": process.stdout.strip(), "instructions": count,
            "seconds": round(time.monotonic() - started, 1), "stderr": process.stderr.strip()[-400:]}


def group(args, workloads, output, label, runtime, profile, options=(), env=None):
    native = run(args, workloads, output, runtime, "native", options, env)
    rows = [run(args, workloads, output, runtime, profile, options, env) for _ in range(args.repeats)]
    counts = [row["instructions"] for row in rows]
    ok = native["exit"] == 0 and all(row["exit"] == 0 and row["stdout"] == native["stdout"] and row["instructions"]
                                     for row in rows)
    median = sorted(counts)[len(counts) // 2] if ok else None
    # Exact integer spread, reported in parts per million.
    spread = (max(counts) - min(counts)) * 1_000_000 // min(counts) if ok else None
    result = {"label": label, "runtime": runtime, "profile": profile, "options": list(options),
              "env": sorted((env or {}).keys()), "ok": ok, "counts": counts, "median": median,
              "spread_ppm": spread, "seconds": [row["seconds"] for row in rows],
              "failure": None if ok else [row["stderr"] for row in rows + [native] if row["exit"] != 0][:1]}
    print(json.dumps({k: result[k] for k in ("runtime", "profile", "label", "ok", "median", "spread_ppm")}),
          flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sdk", type=Path, required=True)
    parser.add_argument("--client", type=Path, required=True, help="libcpuid_profile_client.so")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--profile", default="x86-64-v2", choices=["x86-64-v1", "x86-64-v2"])
    parser.add_argument("--runtime", action="append", choices=["cpython", "node", "java"])
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--engine-debug", action="store_true")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--node", default=shutil.which("node") or "node")
    parser.add_argument("--java", default=shutil.which("java") or "java")
    parser.add_argument("--javac", default=shutil.which("javac") or "javac")
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()
    if not 2 <= args.repeats <= 9:
        raise SystemExit("--repeats must be between 2 and 9")
    if not shutil.which("setarch"):
        raise SystemExit("setarch (util-linux) is required")
    args.output_dir.mkdir(mode=0o700)
    output = args.output_dir.resolve()
    runtimes = args.runtime or ["cpython", "node", "java"]
    workdir = output / "java"
    if "java" in runtimes:
        workdir.mkdir()
        env = {k: v for k, v in os.environ.items() if k != "JAVA_TOOL_OPTIONS"}
        subprocess.run([args.javac, "-d", str(workdir), str(HERE / "Work.java")], check=True, env=env)
    workloads = commands(args, workdir)
    mask = {"GLIBC_TUNABLES": GLIBC_MASK, "OPENSSL_ia32cap": OPENSSL_MASK}
    results = []
    for runtime in runtimes:
        stable, stable_env = DETERMINISM[runtime], DETERMINISM_ENV.get(runtime, {})
        results.append(group(args, workloads, output, "default", runtime, args.profile, DEFAULT[runtime]))
        for profile in ("none", args.profile):
            results.append(group(args, workloads, output, "deterministic", runtime, profile, stable, stable_env))
            results.append(group(args, workloads, output, "deterministic + above-profile mask", runtime,
                                 profile, stable, {**stable_env, **mask}))
    summary = {"profile": args.profile, "repeats": args.repeats, "groups": results,
               "all_ok": all(item["ok"] for item in results)}
    (output / "result.json").write_text(json.dumps(summary, indent=2) + "\n")
    return 0 if summary["all_ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
