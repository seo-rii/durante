#!/usr/bin/env python3
"""Compare the counts of deterministic contest cells across configurations.

  python3 -B experiments/java_timing/parity.py --output evidence/count-parity.json \
      --runner-bin target/release/durante \
      --config main=SDK_MAIN,CLIENT_MAIN --config both=SDK,CLIENT [...]

Runs tests/contest_corpus.py with --no-aslr for the natively compiled,
single-threaded languages (static and dynamic C++, Rust) once per configuration
(an SDK and a client), and records each cell's instruction and REP counts by
mode. Each configuration is first copied to the same path: the runtime passes
its own and the client's paths to the program in its environment, and a
different length changes what the C library's start-up executes. The corpus
output, and so each program's path, is the same directory for the same reason. Counts of these cells are exact, so a runtime or client change that does
not change what is counted gives identical counts. Every configuration must
agree with the first on every (problem, language, mode) whose measurement is
complete; budget-half runs stop at their budget and are compared by state only.
Rust's start-up parses /proc/self/maps (glibc's pthread_getattr_np), which
lists the runtime's and the client's mappings, so its counts follow where they
are mapped: its differences are reported separately and do not fail the check.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tests" / "contest"))
from contest_corpus import LANGS as CORPUS_LANGS  # noqa: E402

LANGS = ("cpp-static", "cpp-dyn", "rust")


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(config, sdk, client, args, work):
    out = work / "run"  # The same path for every configuration, as are the programs' paths.
    shutil.rmtree(out, ignore_errors=True)
    stage = work / "stage"
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir()
    shutil.copytree(sdk, stage / "sdk", symlinks=True)
    shutil.copy2(client, stage / "libdurante.so")
    subprocess.run([sys.executable, str(ROOT / "tests/contest_corpus.py"), "--sdk", str(stage / "sdk"),
                    "--client", str(stage / "libdurante.so"), "--runner-bin", str(args.runner_bin),
                    "--output-dir", str(out),
                    "--no-aslr", "--repeat", "1", *[a for lang in LANGS for a in ("--lang", lang)]],
                   check=True, capture_output=True)
    results = json.loads((out / "results.json").read_text())
    cells = {}
    for row in results["rows"]:
        if row["mode"] == "native":
            continue
        m = (row.get("report") or {}).get("measurement", {})
        key = f'{row["problem"]}/{row["lang"]}/{row["mode"]}'
        cells[key] = {"errors": row["errors"], "valid": m.get("valid"), "complete": m.get("complete"),
                      "instructions": m.get("instructions"), "rep_bytes": m.get("rep_bytes"),
                      "rep_entries": m.get("rep_entries")}
    return cells


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--runner-bin", type=Path, required=True)
    parser.add_argument("--config", action="append", required=True, help="NAME=SDK,CLIENT")
    args = parser.parse_args()
    args.runner_bin = args.runner_bin.resolve()
    configs, provenance = {}, {}
    for spec in args.config:
        name, paths = spec.split("=", 1)
        sdk, client = (Path(p).resolve() for p in paths.split(","))
        configs[name] = (sdk, client)
        provenance[name] = {"libdynamorio_release_sha256": sha256(sdk / "lib64/release/libdynamorio.so"),
                            "client_sha256": sha256(client)}
    with tempfile.TemporaryDirectory(prefix="count-parity-", dir=args.output.resolve().parent) as tmp:
        cells = {name: run(name, sdk, client, args, Path(tmp)) for name, (sdk, client) in configs.items()}
    first = next(iter(cells))
    differences, maps_dependent, compared = [], [], 0
    for key, base in cells[first].items():
        for name in cells:
            other = cells[name].get(key)
            if other is None or other["errors"] or base["errors"]:
                differences.append({"cell": key, "config": name, "reason": "missing or failed"})
                continue
            if "budget-half" in key:
                same = other["valid"] == base["valid"]
            else:
                same = all(other[k] == base[k] for k in ("valid", "complete", "instructions", "rep_bytes",
                                                         "rep_entries"))
                compared += name != first
            if not same:
                lang = key.split("/")[1]
                (maps_dependent if CORPUS_LANGS[lang].reads_maps else differences).append(
                    {"cell": key, "config": name, "base": base, "other": other})
    git = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True)
    result = {"schema": 1, "command": sys.argv, "git_head": git.stdout.strip(), "langs": list(LANGS),
              "configs": provenance, "cells": cells, "compared": compared, "differences": differences,
              "maps_dependent_differences": maps_dependent}
    args.output.write_text(json.dumps(result, indent=1) + "\n")
    print(json.dumps({"compared": compared, "differences": len(differences),
                      "maps_dependent_differences": len(maps_dependent)}))
    return 1 if differences else 0


if __name__ == "__main__":
    raise SystemExit(main())
