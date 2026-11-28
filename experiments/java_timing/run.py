#!/usr/bin/env python3
"""Java CPU time under Durante, per contest problem, with provenance.

  python3 -B experiments/java_timing/run.py --output results.json --rounds 5 --cpus 0,1 \
      --runner-bin target/release/durante \
      --config main=SDK_MAIN,CLIENT_MAIN --config both=SDK,CLIENT [...] \
      --lang java=main,both --lang java-cds=main,both

Each round runs, for every problem and profile, the native command and each
named configuration (an SDK and a client) in a rotated order, as plain runs and
as budget-large runs (a work budget of four times the plain run's work units,
as tests/contest_corpus.py does). The inputs, Java commands, supervisor command
and pinned execution are those of tests/contest_corpus.py. Every sample is kept;
summarize.py derives the tables from them.
"""
import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tests" / "contest"))
import contest_corpus as cc  # noqa: E402
import problems  # noqa: E402

PROBLEMS = list(problems.PROBLEMS)


def sha256(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def first_line(command):
    out = subprocess.run(command, capture_output=True, text=True, env=cc.clean_env())
    return (out.stdout + out.stderr).strip().splitlines()[0]


def provenance(args, configs):
    cpu = next((line.split(":", 1)[1].strip() for line in open("/proc/cpuinfo")
                if line.startswith("model name")), None)
    files = {}
    for name, (sdk, client) in configs.items():
        manifest = sdk / ".durante-runtime.json"
        files[name] = {
            "sdk": str(sdk),
            "libdynamorio_release_sha256": sha256(sdk / "lib64/release/libdynamorio.so"),
            "runtime_manifest_sha256": sha256(manifest) if manifest.is_file() else None,
            "runtime_inputs": (json.loads(manifest.read_text()).get("inputs") if manifest.is_file() else None),
            "client": str(client), "client_sha256": sha256(client)}
    git = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True)
    return {"command": sys.argv, "git_head": git.stdout.strip(), "configs": files,
            "runner_bin_sha256": sha256(args.runner_bin), "cpus": args.cpus, "rounds": args.rounds,
            "java": first_line(["java", "-version"]), "javac": first_line(["javac", "-version"]),
            "cpu_model": cpu, "online_cpus": os.cpu_count(), "kernel": platform.release(),
            "problems_sha256": sha256(ROOT / "tests/contest/problems.py"),
            "corpus_sha256": sha256(ROOT / "tests/contest_corpus.py")}


def sample(argv, case, stdin, expected, env, workdir, cpus):
    row = cc.execute(argv, stdin, 300, env, cc.stack_limit("unlimited"), cpus, workdir)
    return {"exit": row["exit"], "cpu_s": row["cpu_s"], "wall_s": row["wall_s"],
            "maxrss_kb": row["maxrss_kb"], "output_ok": row["stdout"] == expected, **case}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--runner-bin", type=Path, required=True)
    parser.add_argument("--config", action="append", required=True, help="NAME=SDK,CLIENT")
    parser.add_argument("--lang", action="append", required=True, help="java|java-cds=CONFIG,...")
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--cpus", default="0,1")
    parser.add_argument("--problem", action="append", choices=PROBLEMS)
    args = parser.parse_args()
    args.runner_bin = args.runner_bin.resolve()
    configs = {}
    for spec in args.config:
        name, paths = spec.split("=", 1)
        sdk, client = paths.split(",")
        configs[name] = (Path(sdk).resolve(), Path(client).resolve())
    langs = {}
    for spec in args.lang:
        lang, names = spec.split("=", 1)
        langs[lang] = names.split(",")
        assert lang in ("java", "java-cds") and all(n in configs for n in langs[lang]), spec
    selected = args.problem or PROBLEMS
    env = cc.clean_env()
    result = {"schema": 1, "provenance": provenance(args, configs), "langs": langs, "samples": []}
    with tempfile.TemporaryDirectory(prefix="java-timing-") as tmp:
        tmp = Path(tmp)
        cells = {}
        for problem in selected:
            data = problems.generate(problem, "full")
            stdin = tmp / f"{problem}.in"
            stdin.write_bytes(data)
            expected = problems.reference(problem, data).encode()
            result["provenance"].setdefault("input_sha256", {})[problem] = hashlib.sha256(data).hexdigest()
            for lang in langs:
                out = tmp / problem / lang
                out.mkdir(parents=True)
                log = []
                command = cc.LANGS[lang].build(problem, ROOT / "tests/contest" / problem / "Main.java", out, log)
                if any(step["exit"] for step in log):
                    raise SystemExit(f"build failed: {log}")
                cells[(problem, lang)] = (command, stdin, expected)
        for round_index in range(args.rounds):
            for problem in selected:
                for lang, names in langs.items():
                    command, stdin, expected = cells[(problem, lang)]
                    case = {"round": round_index, "problem": problem, "lang": lang}
                    result["samples"].append(sample(command, {**case, "config": "native", "mode": "native"},
                                                    stdin, expected, env, tmp, args.cpus))
                    order = names[round_index % len(names):] + names[:round_index % len(names)]
                    for name in order:
                        sdk, client = configs[name]
                        report = tmp / f"report-{round_index}-{problem}-{lang}-{name}.json"
                        drrun = sdk / "bin64/drrun"
                        ns = argparse.Namespace(runner_bin=args.runner_bin, drrun=drrun, client=client)
                        plain = sample(cc.durante_command(ns, report, 240000) + ["--", *command],
                                       {**case, "config": name, "mode": "durante"}, stdin, expected, env, tmp,
                                       args.cpus)
                        measurement = (json.loads(report.read_text()).get("measurement", {})
                                       if report.is_file() else {})
                        plain["instructions"] = measurement.get("instructions")
                        plain["valid"] = measurement.get("valid")
                        result["samples"].append(plain)
                        units = cc.work_units(measurement) if measurement.get("valid") else None
                        if units:
                            report = tmp / f"report-{round_index}-{problem}-{lang}-{name}-budget.json"
                            budget = sample(cc.durante_command(ns, report, 240000, budget=max(4 * units, 10_000_000))
                                            + ["--", *command], {**case, "config": name, "mode": "budget-large"},
                                            stdin, expected, env, tmp, args.cpus)
                            budget["valid"] = (json.loads(report.read_text()).get("measurement", {}).get("valid")
                                               if report.is_file() else None)
                            result["samples"].append(budget)
                print(f"round {round_index} {problem} done", flush=True)
            args.output.write_text(json.dumps(result, indent=1) + "\n")
    args.output.write_text(json.dumps(result, indent=1) + "\n")


if __name__ == "__main__":
    main()
