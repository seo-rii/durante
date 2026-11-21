#!/usr/bin/env python3
"""Collect two divider feature replays on the original executables, or verify their raw records."""
import argparse
import gzip
import io
import json
from pathlib import Path
import platform
import runpy
import subprocess
import tarfile
from tempfile import TemporaryDirectory

from corpus import HERE, ROOT, corpus, read_member, require, sha

legacy = runpy.run_path(str(HERE.parent / "rust_operand_bytes/collect.py"))
project = legacy["production"]
aggregate = runpy.run_path(str(ROOT/"collect.py"))["collect"]
REPEATS = 2


def sources():
    files = [HERE/n for n in ("collect.py", "corpus.py", "CMakeLists.txt", "probe.c")]
    files.extend([ROOT / "client/latency_bound.h", ROOT / "collect.py", HERE.parent / "rust_operand_bytes/collect.py",
                  HERE.parent / "rust_instruction_mix/collect.py", HERE.parent / "dependency_pairs/study.py"])
    return {p.relative_to(ROOT).as_posix(): sha(p.read_bytes()) for p in files}


def replay_run(files, trial, case, pid):
    from replay import validate
    stdout = files("stdout")
    require(stdout.hex() == case["stdout_hex"] and not files("stderr"), "output mismatch: " + trial)
    raw = files("feature.json")
    feature = json.loads(raw)
    validate(feature, pid)
    name = f"records/process-{pid}.jsonl"
    with TemporaryDirectory() as tmp:
        folder = Path(tmp)
        (folder/Path(name).name).write_bytes(files(name))
        production = project(folder, pid, "operand_bytes", aggregate)
    # The sidecar restricts its block counters to DIV-containing blocks in the main ELF.
    require(sum(b["entries"] * b["instructions"] for b in feature["blocks"]) <= production["instructions"],
            "divider blocks exceed the production instruction count")
    return {"production": production, "feature": feature}


def read_observations(observations, receipts):
    data = json.loads(gzip.decompress(Path(observations).read_bytes()))
    original = corpus()
    require(data["schema"] == 1 and data["repetitions"] == REPEATS and data["sources_sha256"] == sources(),
            "wrong acquisition code or configuration")
    historical = original["tools"]
    require(all(data["tools"][k] == digest for k, digest in historical.items()) and
            data["archived_inputs_sha256"] == original["inputs_sha256"] and
            data["receipts_sha256"] == sha(Path(receipts).read_bytes()), "wrong GCE corpus, tools or archive")
    cases = original["cases"]
    require(data["workloads"] == list(cases) and len(data["samples"]) == len(cases)*REPEATS, "wrong case selection")
    for case in cases.values():
        case["runs"] = []
    with tarfile.open(receipts) as tar:
        members = [m.name for m in tar.getmembers()]
        expected = set()
        for number, row in enumerate(data["samples"]):
            rep, index = divmod(number, len(cases))
            name = list(cases)[index]
            case = cases[name]
            pid = row["pid"]
            trial = f"{rep+1}/{name}"
            require(row["name"] == name and row["repetition"] == rep+1 and type(pid) is int and pid > 0 and
                    row["binary_sha256"] == case["binary_sha256"] and row["exit_code"] == 0 and
                    row["stdin_sha256"] == sha(bytes.fromhex(case["stdin_hex"])), "wrong run identity: " + trial)
            files = ("stdout", "stderr", "feature.json", f"records/process-{pid}.jsonl")
            require(set(row["sha256"]) == set(files), "unexpected receipt files")
            expected.update(f"{trial}/{f}" for f in files)
            def read(f):
                raw = read_member(tar, f"{trial}/{f}")
                require(sha(raw) == row["sha256"][f], "wrong raw file: " + f)
                return raw
            case["runs"].append(replay_run(read, trial, case, pid))
        require(len(members) == len(expected) and set(members) == expected, "extra or missing receipt")
    return cases


def acquire(args):
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    original = corpus(out/"elfs")
    plan = original["tools"]
    drroot = args.dynamorio.resolve()
    tools = {"launcher": drroot/"bin64/drrun", "runtime": drroot/"lib64/release/libdynamorio.so",
             "production_reader": ROOT/"collect.py", "meter": args.meter.resolve(), "divider": args.probe.resolve()}
    identity = {k: sha(p.read_bytes()) for k, p in tools.items()}
    require(all(identity[k] == digest for k, digest in plan.items()), "use the pinned runtime, meter and reader")
    frozen_sources = sources()
    cases = original["cases"]
    samples = []
    # The local process supplies instruction counters only. No local CPU or wall time is recorded.
    for rep in range(1, REPEATS+1):
        for name, case in cases.items():
            trial = out/str(rep)/name
            records = trial/"records"
            records.mkdir(parents=True)
            binary = out/"elfs"/name/"Main"
            require(sha(binary.read_bytes()) == case["binary_sha256"], "changed ELF: " + name)
            cmd = legacy["metered"](tools, trial, binary, "operand_bytes")
            at = cmd.index("-follow_children")
            options = "-out " + legacy["shared"].quoted(trial/"feature.json")
            cmd[at:at] = ["-client", tools["divider"], "00000001", options]
            with subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as proc:
                try:
                    stdout, stderr = proc.communicate(bytes.fromhex(case["stdin_hex"]), timeout=180)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.communicate()
                    raise
                pid = proc.pid
            (trial/"stdout").write_bytes(stdout)
            (trial/"stderr").write_bytes(stderr)
            require(proc.returncode == 0 and not stderr and stdout.hex() == case["stdout_hex"], "client or workload failed: "+name)
            record = f"records/process-{pid}.jsonl"
            require([p.name for p in records.iterdir()] == [Path(record).name], "unexpected meter record set")
            feature = json.loads((trial/"feature.json").read_bytes())
            require(feature["pid"] == pid and feature["threads"] == 1, "wrong divider process identity")
            project(records, pid, "operand_bytes", aggregate)
            samples.append({"name": name, "repetition": rep, "binary_sha256": case["binary_sha256"],
                            "stdin_sha256": sha(bytes.fromhex(case["stdin_hex"])), "pid": pid, "exit_code": proc.returncode,
                            "sha256": {f: sha((trial/f).read_bytes()) for f in ("stdout", "stderr", "feature.json", record)}})
    require(identity == {k: sha(p.read_bytes()) for k,p in tools.items()} and frozen_sources == sources(), "tools changed during acquisition")
    archive = out/"receipts.tar.xz"
    with tarfile.open(archive, "x:xz") as tar:
        for row in samples:
            for f, digest in row["sha256"].items():
                path = f'{row["repetition"]}/{row["name"]}/{f}'
                raw = (out/path).read_bytes()
                require(sha(raw) == digest, "receipt changed during packing: "+path)
                info = tarfile.TarInfo(path)
                info.size = len(raw)
                tar.addfile(info, io.BytesIO(raw))
    meta = {"schema": 1, "repetitions": REPEATS, "feature_host": {"system": platform.system(), "arch": platform.machine(),
            "kernel": platform.release(), "libc": list(platform.libc_ver())}, "workloads": list(cases),
            "archived_inputs_sha256": original["inputs_sha256"], "tools": identity,
            "sources_sha256": frozen_sources, "receipts_sha256": sha(archive.read_bytes()), "samples": samples}
    observations = out/"observations.json.gz"
    observations.write_bytes(gzip.compress((json.dumps(meta, indent=2)+"\n").encode(), mtime=0))
    read_observations(observations, archive)
    print(f"Verified {len(samples)} local divider runs, original meter records, expected outputs and original GCE native controls.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    take = sub.add_parser("collect")
    for key in ("dynamorio", "meter", "probe", "out"):
        take.add_argument("--"+key, required=True, type=Path)
    check = sub.add_parser("verify")
    check.add_argument("observations", type=Path)
    check.add_argument("receipts", type=Path)
    args = parser.parse_args()
    if args.command == "collect":
        acquire(args)
    else:
        cases = read_observations(args.observations, args.receipts)
        print(f"Verified {len(cases)} workloads, {sum(len(c['runs']) for c in cases.values())} local diagnostic runs "
              f"and {sum(len(c['native_ns']) for c in cases.values())} archived GCE native CPU times.")
