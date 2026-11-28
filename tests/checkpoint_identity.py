#!/usr/bin/env python3
"""Check every packed write checkpoint against the full address of its write.

The flat client stores a write checkpoint as one word that names the write by
the low 17 bits of its address; a callback that settles it compares them with
the write it expects (client/durante.c, the packed word). The test build of the
client (tests/checkpoint_identity, DURANTE_TEST_CHECKPOINT_IDENTITY) also stores
each write's full address and checks, at every settled word, that the packed
word is kept exactly when the full address is the expected write. A difference
aborts the process. Each process prints one `durante-checkpoint-identity:` line
with the words it settled, by callback (REP, retry, completed write, other),
those whose full address was another write, and the blocks built with and
without packed words.

Cases:
  fixture    tests/checkpoint_identity.c (a block that writes its own code, REP
             STOS and REPE CMPSB, a far jump) under the supervisor: plain, with
             a work budget (the live clock) and with --latency-bound; and under
             the engine with jump and call elision, whose far block must be
             built without packed words
  jit-stress tests/jit_stress.py, every mode, with the test client
  jit-replay tests/jit_replay.py, whose fixed oracle gives each fixture's exact
             count; the identity line is the only diagnostic it may report
  runtimes   the contest problems in each --lang, plain and with a large work
             budget

Every run must succeed with its reference output and a valid measurement, and
every supervised process must report. The summary sums the counts.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tests" / "contest"))
import contest_corpus as cc  # noqa: E402
import problems  # noqa: E402

LINE = re.compile(r"durante-checkpoint-identity: pid=\d+((?: \w+=\d+)+)")
FIELDS = ("settled", "rep", "retry", "after_write", "other", "unnamed", "foreign",
          "packed_blocks", "unfit_blocks")
DEFAULT_LANGS = ("java", "java-cds", "node", "pypy", "php-jit")


def identity_lines(text):
    return [{k: int(v) for k, v in re.findall(r"(\w+)=(\d+)", m.group(1))}
            for m in LINE.finditer(text)]


def total(lines):
    return {field: sum(line.get(field, 0) for line in lines) for field in FIELDS}


def supervised(args, work, name, command, stdin, budget=None, latency=False, env=None):
    report = work / f"{name}.json"
    argv = cc.durante_command(args, report, args.timeout_ms, budget=budget, latency=latency)
    row = cc.execute(argv + ["--", *command], stdin, args.timeout_ms / 1000 + 30, cc.clean_env(env),
                     cc.stack_limit("unlimited"), args.cpus, work)
    measurement = json.loads(report.read_text()).get("measurement", {}) if report.is_file() else {}
    lines = identity_lines(row["stderr"])
    errors = []
    if row["exit"] != 0:
        errors.append(f"exit {row['exit']}")
    if measurement.get("valid") is not True or measurement.get("complete") is not True:
        errors.append(f"measurement invalid: {measurement.get('reason')}")
    if not lines:
        errors.append("no identity report")
    return {"case": name, "errors": errors, "stdout": row["stdout"], "lines": lines,
            "counts": total(lines), "instructions": measurement.get("instructions"),
            "stderr_tail": row["stderr"][-1500:] if errors else None}


def fixture_cases(args, work):
    executable = work / "checkpoint-identity"
    subprocess.run(["gcc", "-std=c17", "-O2", "-Wall", "-Wextra", "-Werror",
                    str(ROOT / "tests/checkpoint_identity.c"), "-o", str(executable)], check=True)
    command = [str(executable), str(args.rounds)]
    expected = json.dumps({"rounds": args.rounds, "bad": 0}) + "\n"
    stdin = work / "empty.in"
    stdin.write_bytes(b"")
    rows = []
    for name, budget, latency in (("fixture-plain", None, False),
                                  ("fixture-live", 10 ** 12, False),
                                  ("fixture-latency", None, True)):
        row = supervised(args, work, name, command, stdin, budget=budget, latency=latency)
        counts = row["counts"]
        if row["stdout"].decode() != expected:
            row["errors"].append("fixture failed: " + row["stdout"].decode()[:200])
        if counts["after_write"] < args.rounds or counts["rep"] < 2 * args.rounds or not counts["retry"]:
            row["errors"].append("a completed write, REP or retry settled no packed word")
        rows.append(row)
    # The engine alone, with elision: the far block spans more than 128 KiB.
    records = work / "elision-records"
    records.mkdir()
    proc = subprocess.run([str(args.drrun), "-quiet", "-disable_traces", "-max_elide_jmp", "16",
                           "-max_elide_call", "16", "-c", str(args.client), "-outdir", str(records),
                           "-rep_bytes", "--", *command], capture_output=True, timeout=args.timeout_ms / 1000,
                          env=cc.clean_env())
    lines = identity_lines(proc.stderr.decode("utf-8", "replace"))
    row = {"case": "fixture-elision", "errors": [], "lines": lines, "counts": total(lines),
           "stdout": proc.stdout}
    if proc.returncode != 0 or proc.stdout.decode() != expected:
        row["errors"].append(f"exit {proc.returncode}: {proc.stdout.decode()[:200]}")
    if not lines or not row["counts"]["unfit_blocks"]:
        row["errors"].append("no block was built without packed words")
    rows.append(row)
    return rows


def jit_stress_cases(args, work):
    out = work / "jit-stress"
    proc = subprocess.run([sys.executable, str(ROOT / "tests/jit_stress.py"), "--sdk", str(args.sdk),
                           "--client", str(args.client), "--runner-bin", str(args.runner_bin),
                           "--output-dir", str(out), "--repeats", "1"], capture_output=True, text=True)
    lines = []
    for path in sorted(out.rglob("stderr.txt")):
        if "-supervisor-" in path.parent.name:
            found = identity_lines(path.read_text(errors="replace"))
            if not found:
                lines.append(None)
            lines.extend(found)
    errors = [] if proc.returncode == 0 else ["jit_stress.py failed: " + proc.stdout[-500:]]
    if None in lines or not lines:
        errors.append("a supervised case printed no identity report")
    lines = [line for line in lines if line]
    return [{"case": "jit-stress", "errors": errors, "lines": lines, "counts": total(lines)}]


DIAGNOSTIC = "unexpected runtime or application diagnostic"


def jit_replay_cases(args, work):
    out = work / "jit-replay"
    subprocess.run([sys.executable, str(ROOT / "tests/jit_replay.py"), "--sdk", str(args.sdk),
                    "--client", str(args.client), "--runner-bin", str(args.runner_bin),
                    "--output-dir", str(out)], capture_output=True, text=True)
    results = json.loads((out / "results.json").read_text())
    lines, errors, exact = [], [], 0
    for row in results["rows"]:
        found = identity_lines(row.get("stderr") or "")
        if row.get("execution_mode") in ("native", "engine"):  # No client.
            if row["errors"]:
                errors.append(f'{row["case"]}: {row["errors"]}')
            continue
        m = row.get("measurement") or {}
        # The identity line is the one diagnostic the test client adds.
        if [e for e in row["errors"] if e != DIAGNOSTIC] or not found:
            errors.append(f'{row["case"]}: {row["errors"]}, {len(found)} identity reports')
        elif row["exit_code"] != 0 or m.get("valid") is not True or \
                m.get("instructions") != row["expected_instructions"]:
            errors.append(f'{row["case"]}: {m.get("instructions")} instructions, '
                          f'oracle {row["expected_instructions"]}')
        else:
            exact += 1
        lines += found
    if not exact:
        errors.append("no instrumented replay case")
    return [{"case": "jit-replay", "errors": errors, "lines": lines, "counts": total(lines),
             "oracle_exact": exact}]


def runtime_cases(args, work):
    rows = []
    for problem in args.problems:
        for lang in args.langs:
            spec = cc.LANGS[lang]
            binary = {"java": "java", "java-cds": "java", "node": "node", "php-jit": "php"}.get(lang, spec.runtime)
            if binary and shutil.which(binary) is None:
                rows.append({"case": f"{problem}-{lang}", "errors": [], "skipped": "runtime not found",
                             "lines": [], "counts": total([])})
                continue
            data = problems.generate(problem, spec.tier_for(problem))
            stdin = work / f"{problem}-{lang}.in"
            stdin.write_bytes(data)
            expected = problems.reference(problem, data).encode()
            out = work / f"{problem}-{lang}"
            out.mkdir()
            log = []
            command = spec.build(problem, ROOT / "tests/contest" / problem / spec.source, out, log)
            env = cc.EXTRA_ENV.get((lang, problem))
            for mode, budget in (("plain", None), ("live", 10 ** 13)):
                name = f"{problem}-{lang}-{mode}"
                row = supervised(args, work, name, command, stdin, budget=budget, env=env)
                if row["stdout"] != expected:
                    row["errors"].append("wrong output")
                rows.append(row)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for flag in ("sdk", "client", "runner-bin", "output-dir"):
        parser.add_argument("--" + flag, type=Path, required=True)
    parser.add_argument("--lang", action="append", dest="langs", choices=sorted(cc.LANGS))
    parser.add_argument("--problem", action="append", dest="problems", choices=list(problems.PROBLEMS))
    parser.add_argument("--rounds", type=int, default=2000)
    parser.add_argument("--cpus", default="0,1")
    parser.add_argument("--timeout-ms", type=int, default=240000)
    parser.add_argument("--no-jit-stress", action="store_true")
    args = parser.parse_args()
    args.langs = args.langs or list(DEFAULT_LANGS)
    args.problems = args.problems or list(problems.PROBLEMS)
    args.sdk, args.client, args.runner_bin = (p.resolve() for p in (args.sdk, args.client, args.runner_bin))
    args.drrun = args.sdk / "bin64/drrun"
    args.output_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="cases-", dir=args.output_dir.resolve()))
    rows = fixture_cases(args, work)
    rows += jit_replay_cases(args, work)
    if not args.no_jit_stress:
        rows += jit_stress_cases(args, work)
    rows += runtime_cases(args, work)
    for row in rows:
        row.pop("stdout", None)
    counts = total([row["counts"] for row in rows])
    failures = [row["case"] for row in rows if row["errors"]]
    summary = {"exit_code": 1 if failures or not counts["settled"] else 0,
               "cases": len(rows), "failures": failures,
               "skipped": [row["case"] for row in rows if row.get("skipped")],
               "counts": counts,
               "client_sha256": hashlib.sha256(args.client.read_bytes()).hexdigest(),
               "rows": rows}
    (args.output_dir / "results.json").write_text(json.dumps(summary, indent=1) + "\n")
    print(json.dumps({k: v for k, v in summary.items() if k != "rows"}))
    return summary["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
