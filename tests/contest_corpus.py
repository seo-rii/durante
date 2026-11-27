#!/usr/bin/env python3
"""Run contest-style submissions natively and under every `durante run` mode.

Each problem in tests/contest/problems.py has a deterministic input and an
independent reference answer. Every selected problem x language cell runs:

  native        the submission alone
  durante       `durante run` without a budget (repeated for compiled code)
  budget-large  `--work-budget` well above the measured work
  budget-half   `--work-budget` at half the measured work (must stop live)
  latency       `durante run --latency-bound`

The harness checks outputs, execution states, measurement validity, budget
receipts and repeatability, and records CPU time for every run. It writes
results.json in a fresh output directory and prints a compact summary.

Every command runs under `taskset -c 0,1` (--cpus) with JAVA_TOOL_OPTIONS and
_JAVA_OPTIONS removed and RLIMIT_STACK unlimited (aonohako's default is the
hard limit), which the depth-1e6 recursion in deep_dfs needs. Dynamically
linked code varies by a few instructions with ASLR, so its repeats must match
exactly only with --no-aslr (otherwise a difference is a warning). Rust's
start-up parses /proc/self/maps, which also lists the runtime's mappings, so
its count with --latency-bound may differ from the plain count (a warning).

  python3 tests/contest_corpus.py --sdk SDK --client build/libdurante.so \\
      --runner-bin target/release/durante --output-dir NEW_DIR [--lang L] [--problem P]
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import shutil
import signal
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
CONTEST = ROOT / "tests/contest"
sys.path.insert(0, str(CONTEST))
import problems  # noqa: E402

MODES = ("native", "durante", "budget-large", "budget-half", "latency")
# Typical judge settings (aonohako's Java/Node defaults, with more heap so the
# largest inputs fit). Deep recursion settings are per problem, below.
JAVA_FLAGS = ("-XX:ReservedCodeCacheSize=64m", "-XX:-UseCompressedClassPointers", "-Xmx256m",
              "-Xss16384k", "-XX:MaxDirectMemorySize=16m", "-XX:MaxMetaspaceSize=64m",
              "-Dfile.encoding=UTF-8", "-XX:+UseSerialGC", "-DONLINE_JUDGE=1")
NODE_FLAGS = ("--disable-wasm-trap-handler", "--max-old-space-size=256", "--max-semi-space-size=1")
PHP_JIT_FLAGS = ("-d", "opcache.enable_cli=1", "-d", "opcache.jit=tracing", "-d", "opcache.jit_buffer_size=64M")
CXX_FLAGS = ("-O2", "-std=c++20")
# Environment that runtimes would otherwise echo to stderr or that changes them.
DROP_ENV = ("JAVA_TOOL_OPTIONS", "_JAVA_OPTIONS", "JDK_JAVA_OPTIONS")


class Lang:
    def __init__(self, name, source, tier, deterministic=False, runtime=None, reads_maps=False):
        self.name, self.source, self.tier = name, source, tier
        self.deterministic = deterministic  # natively compiled, single-threaded result
        self.runtime = runtime
        self.reads_maps = reads_maps  # start-up parses /proc/self/maps

    def exact(self, no_aslr):
        """Whether repeated counts must be equal."""
        return self.deterministic and (no_aslr or self.name == "cpp-static")

    def tier_for(self, problem):
        if self.name == "pypy" and problem == "deep_dfs":
            return "lite"  # PyPy's deep recursion on a thread takes ~5 s natively at full size.
        return self.tier

    def build(self, problem, src, out, log):
        """Return the command that runs the built submission."""
        if self.name.startswith("cpp"):
            compiler = "clang++" if self.name == "cpp-clang" else "g++"
            extra = ("-static",) if self.name == "cpp-static" else ()
            binary = out / "sol"
            compile_step([compiler, *CXX_FLAGS, *extra, str(src), "-o", str(binary)], log)
            return [str(binary)]
        if self.name == "rust":
            binary = out / "sol"
            compile_step(["rustc", "-O", "--edition", "2021", str(src), "-o", str(binary)], log)
            return [str(binary)]
        if self.name == "go":
            binary = out / "sol"
            compile_step(["go", "build", "-o", str(binary), str(src)], log)
            return [str(binary)]
        if self.name == "java":
            classes = out / "classes"
            classes.mkdir()
            compile_step(["javac", "-J-Xmx256m", "--release", "17", "-encoding", "UTF-8", "-d", str(classes), str(src)], log)
            return ["java", *JAVA_FLAGS, "-cp", str(classes), "Main"]
        if self.name == "node":
            flags = list(NODE_FLAGS)
            # Depth 1e6: V8 needs a raised --stack-size; the stack rlimit backs it.
            flags.append("--stack-size=500000" if problem == "deep_dfs" else "--stack-size=2048")
            return ["node", *flags, str(src)]
        if self.name == "php-jit":
            return ["php", *PHP_JIT_FLAGS, str(src)]
        return [self.runtime, str(src)]


LANGS = {lang.name: lang for lang in (
    Lang("cpp-static", "sol.cpp", "full", True),
    Lang("cpp-dyn", "sol.cpp", "full", True),
    Lang("cpp-clang", "sol.cpp", "full", True),
    Lang("rust", "sol.rs", "full", True, reads_maps=True),
    Lang("go", "sol.go", "full"),
    Lang("java", "Main.java", "full"),
    Lang("node", "sol.js", "full"),
    Lang("pypy", "sol.py", "full", runtime="pypy3"),
    Lang("python", "sol.py", "lite", runtime="python3"),
    Lang("ruby", "sol.rb", "lite", runtime="ruby"),
    Lang("perl", "sol.pl", "lite", runtime="perl"),
    Lang("php", "sol.php", "lite", runtime="php"),
    Lang("php-jit", "sol.php", "lite"),
    Lang("lua", "sol.lua", "lite", runtime="lua5.4"),
)}
# Ruby's deep_dfs recursion runs on a thread whose VM stack this sizes; Rust, Java and
# PyPy size their own threads in the submission.
EXTRA_ENV = {("ruby", "deep_dfs"): {"RUBY_THREAD_VM_STACK_SIZE": str(512 << 20)}}


def compile_step(command, log):
    proc = subprocess.run(command, capture_output=True, text=True, timeout=300)
    log.append({"command": command, "exit": proc.returncode, "stderr": proc.stderr[-2000:]})
    if proc.returncode != 0:
        raise RuntimeError("compile failed: " + " ".join(command) + "\n" + proc.stderr[-2000:])


def clean_env(extra=None):
    env = {k: v for k, v in os.environ.items() if k not in DROP_ENV}
    env.update(extra or {})
    return env


def stack_limit(value):
    if value == "unlimited":
        return resource.RLIM_INFINITY
    return int(value) << 20


def execute(command, stdin_path, timeout_s, env, stack, cpus, workdir, no_aslr=False):
    """Run one command pinned to `cpus`; return exit status, times and outputs."""
    def limits():
        hard = resource.getrlimit(resource.RLIMIT_STACK)[1]
        soft = stack if hard == resource.RLIM_INFINITY or (stack != resource.RLIM_INFINITY and stack <= hard) else hard
        resource.setrlimit(resource.RLIMIT_STACK, (soft, hard))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    # The personality flag is inherited, so the whole measured tree runs without ASLR.
    full = (["setarch", "x86_64", "-R"] if no_aslr else []) + ["taskset", "-c", cpus, *command]
    out_path, err_path = workdir / "stdout.tmp", workdir / "stderr.tmp"
    with open(stdin_path, "rb") as stdin, open(out_path, "wb") as out, open(err_path, "wb") as err:
        started = time.monotonic()
        proc = subprocess.Popen(full, stdin=stdin, stdout=out, stderr=err, env=env,
                                preexec_fn=limits, start_new_session=True)
        killed = []

        def kill():
            killed.append(True)
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

        timer = threading.Timer(timeout_s, kill)
        timer.start()
        _, status, usage = os.wait4(proc.pid, 0)
        timer.cancel()
        wall = time.monotonic() - started
        proc.returncode = os.waitstatus_to_exitcode(status)
    try:  # Reap anything left in the session (should be nothing).
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    stdout = out_path.read_bytes()
    stderr = err_path.read_bytes()
    out_path.unlink()
    err_path.unlink()
    return {"exit": proc.returncode, "wall_s": round(wall, 4),
            "cpu_s": round(usage.ru_utime + usage.ru_stime, 4),
            "user_s": round(usage.ru_utime, 4), "sys_s": round(usage.ru_stime, 4),
            "maxrss_kb": usage.ru_maxrss, "harness_killed": bool(killed),
            "stdout": stdout, "stderr": stderr.decode("utf-8", "replace")}


def durante_command(args, report, timeout_ms, budget=None, latency=False):
    command = [str(args.runner_bin), "run", "--drrun", str(args.drrun), "--client", str(args.client),
               "--report", str(report), "--timeout-ms", str(timeout_ms)]
    if budget is not None:
        command += ["--work-budget", str(budget)]
    if latency:
        command.append("--latency-bound")
    return command


def excerpt(report):
    e = report.get("execution", {})
    m = report.get("measurement", {})
    keep_m = ("valid", "complete", "reason", "instructions", "rep_bytes", "rep_entries", "quality",
              "signals_observed", "unsupported_rep_entries", "process_count", "image_count",
              "rep_telemetry_status", "latency_bound", "rep_breakdown")
    return {"execution": {k: e.get(k) for k in ("state", "exit_code", "signal", "wall_time_ms",
                                               "cleanup_complete", "records_cleanup_complete",
                                               "supervisor_reaped_processes", "work_budget_killed_processes",
                                               "cancel_signal") if k in e},
            "measurement": {k: m[k] for k in keep_m if k in m},
            "work_budget": report.get("work_budget"),
            "latency_bound_version": report.get("latency_bound_version")}


def work_units(m):
    rb = m.get("rep_breakdown") or {}
    return m["instructions"] + rb.get("movs_bytes", 0) + rb.get("stos_bytes", 0)


def run_mode(args, cell, mode, rep_index=0, budget=None):
    lang, problem = cell["lang"], cell["problem"]
    workdir = args.output_dir / "runs" / f"{problem}-{lang}"
    workdir.mkdir(parents=True, exist_ok=True)
    tag = mode + (f"-{rep_index}" if rep_index else "")
    report = workdir / (tag + ".json")
    timeout_ms = args.latency_timeout_ms if mode == "latency" else args.timeout_ms
    if mode == "native":
        command = cell["command"]
    else:
        command = durante_command(args, report, timeout_ms, budget=budget, latency=mode == "latency") + ["--", *cell["command"]]
    row = execute(command, cell["input"], timeout_ms / 1000 + 30, cell["env"], args.stack, args.cpus, workdir,
                  args.no_aslr)
    stdout = row.pop("stdout")
    row.update(problem=problem, lang=lang, mode=tag, budget=budget, errors=[], warnings=[],
               command=command, output_ok=stdout == cell["expected"],
               stdout_sha256=hashlib.sha256(stdout).hexdigest(), stdout_bytes=len(stdout))
    if not row["output_ok"]:
        row["stdout_head"] = stdout[:120].decode("utf-8", "replace")
    row["stderr"] = row["stderr"][-3000:]
    if mode != "native":
        try:
            data = json.loads(report.read_bytes())
            row["report"] = excerpt(data)
        except (OSError, ValueError) as error:
            row["report"] = None
            row["errors"].append(f"report unavailable: {error}")
    check(row, mode)
    if row["errors"] and report.exists():
        row["report_path"] = str(report)  # keep the full report of a failure
    elif report.exists():
        report.unlink()
    return row


def check(row, mode):
    err = row["errors"]
    if row["harness_killed"]:
        err.append("harness wall limit reached")
    if mode != "budget-half" and not row["output_ok"]:
        err.append("output differs from reference")
    if row["stderr"].strip() and mode != "budget-half":
        row["warnings"].append("stderr not empty")
    if mode == "native":
        if row["exit"] != 0:
            err.append(f"native exit {row['exit']}")
        return
    rep = row.get("report")
    if not rep:
        return
    e, m, wb = rep["execution"], rep["measurement"], rep["work_budget"]
    if not (e.get("cleanup_complete") and e.get("records_cleanup_complete")):
        err.append("cleanup incomplete")
    if mode in ("budget-half", "budget-large") and wb and wb.get("limit") != row["budget"]:
        err.append("receipt limit differs from the requested budget")
    if mode == "budget-half":
        if row["exit"] != 123 or e.get("state") != "work_budget_exceeded":
            err.append(f"expected work_budget_exceeded/123, got {e.get('state')}/{row['exit']}")
        if not wb or wb.get("status") != "exceeded" or wb.get("observation") != "live":
            err.append(f"expected exceeded/live receipt, got {wb and (wb.get('status'), wb.get('observation'))}")
        elif wb["instructions"] + wb["movs_bytes"] + wb["stos_bytes"] <= row["budget"]:
            err.append("receipt does not exceed its limit")
        if m.get("complete") or m.get("instructions") is not None:
            err.append("forced stop exposed a complete measurement")
        return
    if row["exit"] != 0 or e.get("state") != "complete" or e.get("exit_code") != 0:
        err.append(f"expected complete/0 (target 0), got {e.get('state')}/{row['exit']} target {e.get('exit_code')} signal {e.get('signal')}")
    if not (m.get("valid") and m.get("complete")):
        err.append(f"measurement invalid: {m.get('reason')}")
        return
    if mode == "budget-large":
        rb = m.get("rep_breakdown") or {}
        if not wb or wb.get("status") != "within_budget" or wb.get("observation") != "terminal":
            err.append(f"expected within_budget/terminal, got {wb and (wb.get('status'), wb.get('observation'))}")
        elif (wb["instructions"], wb["movs_bytes"], wb["stos_bytes"]) != (
                m["instructions"], rb.get("movs_bytes"), rb.get("stos_bytes")):
            err.append("terminal receipt differs from measurement")
        elif work_units(m) > row["budget"]:
            err.append("terminal work exceeds the requested budget")
    elif wb is not None:
        err.append("unrequested work budget receipt")
    if mode == "latency":
        lb = m.get("latency_bound") or {}
        if rep.get("latency_bound_version") != 1 or lb.get("version") != 1:
            err.append("latency bound missing")
        elif lb["cycles_x16"] < 4 * m["instructions"]:
            err.append("latency bound below issue cost")


def run_cell(args, cell):
    rows = []
    native = run_mode(args, cell, "native")
    rows.append(native)
    if args.native_only:
        return {"problem": cell["problem"], "lang": cell["lang"], "tier": cell["tier"],
                "input_sha256": cell["input_sha256"], "errors": [f"native: {e}" for e in native["errors"]],
                "warnings": [f"native: {w}" for w in native["warnings"]]}, rows
    base = []
    repeats = args.repeat if LANGS[cell["lang"]].deterministic or args.repeat_all else 1
    for index in range(repeats):
        base.append(run_mode(args, cell, "durante", index))
    rows.extend(base)
    good = [r for r in base if not r["errors"]]
    summary = {"problem": cell["problem"], "lang": cell["lang"], "tier": cell["tier"],
               "input_sha256": cell["input_sha256"], "errors": [], "warnings": []}
    if good:
        counts = [r["report"]["measurement"]["instructions"] for r in good]
        units = [work_units(r["report"]["measurement"]) for r in good]
        summary["instructions"] = counts
        if len(base) > 1 and len(set(counts)) > 1:
            msg = f"instruction counts differ across repeats: {counts}"
            (summary["errors"] if LANGS[cell["lang"]].exact(args.no_aslr) else summary["warnings"]).append(msg)
        unit = min(units)
        rows.append(run_mode(args, cell, "budget-large", budget=max(4 * unit, 10_000_000)))
        half = run_mode(args, cell, "budget-half", budget=unit // 2)
        bwall = min(r["report"]["execution"]["wall_time_ms"] for r in good)
        hwall = (half.get("report") or {}).get("execution", {}).get("wall_time_ms")
        half["half_wall_ratio"] = round(hwall / bwall, 3) if hwall is not None and bwall else None
        if hwall is not None and hwall >= args.timeout_ms / 2:
            half["errors"].append(f"half-budget stop took {hwall} ms (timeout {args.timeout_ms})")
        elif half["half_wall_ratio"] is not None and half["half_wall_ratio"] > 1.0:
            half["warnings"].append(f"half-budget stop slower than full run ({half['half_wall_ratio']})")
        rows.append(half)
    else:
        summary["errors"].append("no valid durante baseline; budget modes skipped")
    latency = run_mode(args, cell, "latency")
    rows.append(latency)
    spec = LANGS[cell["lang"]]
    if good and not latency["errors"] and spec.exact(args.no_aslr):
        lat_count = latency["report"]["measurement"]["instructions"]
        if lat_count not in summary["instructions"]:
            msg = f"--latency-bound changed instructions: {lat_count} vs {summary['instructions']}"
            (summary["warnings"] if spec.reads_maps else summary["errors"]).append(msg)
    for r in rows:
        for e in r["errors"]:
            summary["errors"].append(f"{r['mode']}: {e}")
        for w in r["warnings"]:
            summary["warnings"].append(f"{r['mode']}: {w}")
    return summary, rows


def prepare_inputs(args, selected_problems, selected_langs):
    cache = args.input_cache
    cache.mkdir(parents=True, exist_ok=True)
    version = hashlib.sha256((CONTEST / "problems.py").read_bytes()).hexdigest()[:16]
    paths = {}
    for problem in selected_problems:
        for tier in sorted({LANGS[lang].tier_for(problem) for lang in selected_langs}):
            stem = cache / f"{problem}-{tier}-{version}"
            data_path, ans_path = stem.with_suffix(".in"), stem.with_suffix(".ans")
            if not (data_path.exists() and ans_path.exists()):
                data = problems.generate(problem, tier)
                answer = problems.reference(problem, data)
                tmp = stem.with_suffix(".in.tmp")
                tmp.write_bytes(data)
                ans_path.write_text(answer)
                tmp.rename(data_path)
            data = data_path.read_bytes()
            paths[problem, tier] = (data_path, ans_path.read_bytes(), hashlib.sha256(data).hexdigest())
    return paths


def table(summaries, rows):
    status = {}
    for r in rows:
        mode = r["mode"].split("-")[0] if r["mode"].startswith("durante") else r["mode"]
        key = (r["problem"], r["lang"], mode)
        status[key] = status.get(key, True) and not r["errors"]
    lines = ["problem/lang".ljust(22) + " ".join(m[:12].ljust(12) for m in MODES)]
    for s in summaries:
        cells = []
        for m in MODES:
            ok = status.get((s["problem"], s["lang"], m))
            cells.append(("-" if ok is None else "ok" if ok else "FAIL").ljust(12))
        lines.append(f"{s['problem']}/{s['lang']}".ljust(22) + " ".join(cells))
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for name in ("sdk", "client", "runner-bin", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--input-cache", type=Path, help="reusable generated inputs (default: OUTPUT/inputs)")
    parser.add_argument("--problem", action="append", choices=problems.PROBLEMS)
    parser.add_argument("--lang", action="append", choices=tuple(LANGS))
    parser.add_argument("--timeout-ms", type=int, default=120_000)
    parser.add_argument("--latency-timeout-ms", type=int, default=240_000)
    parser.add_argument("--repeat", type=int, default=3, help="durante repeats for deterministic native code")
    parser.add_argument("--repeat-all", action="store_true", help="repeat every language, not only native code")
    parser.add_argument("--stack-limit", default="unlimited",
                        help="RLIMIT_STACK in MiB or 'unlimited' (aonohako defaults to the hard limit)")
    parser.add_argument("--cpus", default="0,1", help="taskset CPU list for every run")
    parser.add_argument("--native-only", action="store_true", help="validate submissions without durante")
    parser.add_argument("--no-aslr", action="store_true",
                        help="run every command under setarch -R; dynamically linked code otherwise varies by a few instructions")
    args = parser.parse_args(argv)
    for name in ("sdk", "client", "runner_bin", "output_dir"):
        setattr(args, name, getattr(args, name).absolute())
    args.input_cache = (args.input_cache or args.output_dir / "inputs").absolute()
    args.drrun = args.sdk / "bin64/drrun"
    args.stack = stack_limit(args.stack_limit)
    args.output_dir.mkdir(parents=True, exist_ok=False)
    selected_problems = args.problem or list(problems.PROBLEMS)
    selected_langs = args.lang or list(LANGS)
    missing = [lang for lang in selected_langs
               if not shutil.which({"cpp-clang": "clang++", "rust": "rustc", "go": "go", "java": "java",
                                    "node": "node", "php-jit": "php"}.get(lang, LANGS[lang].runtime or "g++"))]
    if missing:
        parser.error("toolchain missing for: " + ", ".join(missing))
    inputs = prepare_inputs(args, selected_problems, selected_langs)
    builds, cells, build_failures = [], [], []
    for problem in selected_problems:
        for lang in selected_langs:
            spec = LANGS[lang]
            src = CONTEST / problem / spec.source
            if not src.is_file():
                build_failures.append({"problem": problem, "lang": lang, "error": f"submission source missing: {src}"})
                continue
            out = args.output_dir / "build" / problem / lang
            out.mkdir(parents=True)
            log = []
            try:
                command = spec.build(problem, src, out, log)
            except (RuntimeError, OSError, subprocess.SubprocessError) as error:
                build_failures.append({"problem": problem, "lang": lang, "error": str(error)[:2000]})
                continue
            finally:
                builds.extend(log)
            tier = spec.tier_for(problem)
            data_path, expected, digest = inputs[problem, tier]
            cells.append({"problem": problem, "lang": lang, "tier": tier, "command": command,
                          "input": data_path, "expected": expected, "input_sha256": digest,
                          "env": clean_env(EXTRA_ENV.get((lang, problem)))})
    summaries, rows = [], []
    results = args.output_dir / "results.json"
    def write_results():
        results.write_text(json.dumps({"schema": 1, "build_failures": build_failures, "builds": builds,
                                       "summaries": summaries, "rows": rows,
                                       "settings": {"timeout_ms": args.timeout_ms, "stack_limit": args.stack_limit,
                                                    "latency_timeout_ms": args.latency_timeout_ms,
                                                    "cpus": args.cpus, "repeat": args.repeat, "no_aslr": args.no_aslr}},
                                      indent=1, default=str) + "\n")

    write_results()
    started = time.monotonic()
    for cell in cells:
        summary, cell_rows = run_cell(args, cell)
        summaries.append(summary)
        rows.extend(cell_rows)
        state = "ok" if not summary["errors"] else "FAIL"
        print(f"[{time.monotonic() - started:7.1f}s] {cell['problem']}/{cell['lang']}: {state}"
              + "".join("\n    " + e for e in summary["errors"][:6]), flush=True)
        for row in cell_rows:
            row["command"] = [str(c) for c in row["command"]]
        write_results()
    print(table(summaries, rows))
    for failure in build_failures:
        print("BUILD FAIL", failure["problem"], failure["lang"], failure["error"][:300])
    failed = [s for s in summaries if s["errors"]] + build_failures
    print(f"{len(summaries)} cells, {len(failed)} failing; results in {results}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
