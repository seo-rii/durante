#!/usr/bin/env python3
"""Collect native/dual-client/native triplets for the fixed Rust reference corpus on Linux."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def digest(path):
    return sha(path.read_bytes())


def save(path, value):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def validate_cases(corpus, protocol):
    require(corpus["corpus_id"] == protocol["corpus_id"] and
            corpus["aonohako_commit"] == protocol["aonohako_commit"], "corpus identity mismatch")
    cases = []
    for name in protocol["programs"]:
        program = corpus["programs"][name]
        require([c["scale"] for c in program["cases"]] == protocol["scales"], "scale mismatch")
        for case in program["cases"]:
            require(sha(case["stdin"].encode()) == case["input_sha256"] and
                    sha(case["stdout"].encode()) == case["expected_sha256"], "corpus byte mismatch")
            cases.append((name, case))
    require(len(cases) == len(protocol["cases"]), "case count mismatch")
    for (name, case), archived in zip(cases, protocol["cases"]):
        require(name == archived["workload_id"] and corpus["programs"][name]["source_sha256"] ==
                archived["source_sha256"] and all(case[k] == archived[k] for k in
                ("scale", "input_sha256", "expected_sha256")), "reference case mismatch")
    return cases


def compile_programs(corpus, source, out, rustc, data):
    binaries = {}
    for name, program in corpus["programs"].items():
        folder = out / "bin" / name
        folder.mkdir()
        local_source = folder / "Main.rs"
        shutil.copyfile(source / f"{name}.rs", local_source)
        require(digest(local_source) == program["source_sha256"], f"{name} source mismatch")
        flags = [*corpus["compiler_flags"], "-o", "Main", "Main.rs"]
        with (folder / "compile.log").open("wb") as log:
            subprocess.run([rustc, *flags], cwd=folder, stdout=log, stderr=log, check=True, timeout=120)
        binary = folder / "Main"
        binaries[name] = binary
        data["programs"][name] = {"source_sha256": digest(local_source), "binary_sha256": digest(binary)}
        save(out / "dataset.json", data)
    return binaries


def quoted(path):
    text = str(path)
    require(not any(char in text for char in ('"', "'", "`", '\n', '\r', ';')),
            "unsupported DynamoRIO option path")
    return '"' + text + '"'


def metered_command(tools, run, binary):
    options = f'-outdir {quoted(run / "records")} -rep_bytes -cpuid_profile x86-64-v2'
    return [tools["launcher"], "-quiet", "-client", tools["meter"], "00000000", options,
            "-client", tools["probe"], "00000001", "-out " + quoted(run / "features.json"),
            "-follow_children", "-disable_traces", "-vm_size", "32M", "-vmheap_size", "32M", "--", binary]


def run_phase(command, run, expected, cpu):
    """Fork/exec directly, time the specific reaped child (user + system)."""
    require(os.sched_getaffinity(0) == {cpu}, "collector escaped pinned CPU")
    start = time.monotonic_ns()
    with (run.parent / "stdin").open("rb") as inp, (run / "stdout").open("wb") as stdout, \
            (run / "stderr").open("wb") as stderr:
        pid = os.fork()
        if pid == 0:
            try:
                os.setsid()
                os.dup2(inp.fileno(), 0)
                os.dup2(stdout.fileno(), 1)
                os.dup2(stderr.fileno(), 2)
                os.execv(command[0], command)
            finally:
                os._exit(127)
        try:
            signal.alarm(180)
            _, status, usage = os.wait4(pid, 0)
        except BaseException:
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                # The child might not have reached setsid yet.
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            os.wait4(pid, 0)
            raise
        finally:
            signal.alarm(0)
    wall = time.monotonic_ns() - start
    user = round(usage.ru_utime * 1e9)
    system = round(usage.ru_stime * 1e9)
    result = {"pid": pid, "user_ns": user, "system_ns": system, "cpu_time_ns": user + system,
              "wall_ns": wall, "stdout_sha256": digest(run / "stdout"),
              "exit_code": os.waitstatus_to_exitcode(status)}
    save(run / "phase.json", result)
    require(result["exit_code"] == 0 and result["cpu_time_ns"] > 0 and
            result["stdout_sha256"] == expected, f"{run.name} execution failed in {run.parent.name}")
    return result


def feature_result(run, pid, aggregate):
    receipt = aggregate(run / "records", pid)
    save(run / "production.json", receipt)
    require(receipt.get("complete") is True and receipt["valid"] is True and
            receipt["root_pid"] == pid and receipt["quality"] == "uninterrupted" and
            receipt["cpuid_profile"] == "x86-64-v2" and receipt["signals_observed"] == 0 and
            receipt["image_count"] == receipt["process_count"] == receipt["segments"][0]["threads"] == 1 and
            receipt["processes"][0]["exit_code"] == 0 and receipt["unsupported_rep_entries"] == 0 and
            receipt["rep_bytes_enabled"] is True and receipt["rep_breakdown"]["version"] == 1,
            f"invalid production receipt in {run.parent.name}")
    production = {k: receipt[k] for k in ("complete", "valid", "instructions", "rep_bytes",
                   "signals_observed", "quality", "unsupported_rep_entries", "process_count",
                   "image_count", "cpuid_profile")}
    production.update(threads=receipt["segments"][0]["threads"],
                      rep_movs_bytes=receipt["rep_breakdown"]["movs_bytes"],
                      rep_stos_bytes=receipt["rep_breakdown"]["stos_bytes"])
    features = json.loads((run / "features.json").read_bytes())
    require(features["instructions"] == production["instructions"] and features["bpred"] is False and
            features["mispredicts"] == features["cbr_executed"] == 0, "dual-client counters disagree")
    keys = {"instructions", "loads", "stores", "cond_branches", "direct_jumps_calls", "indirect_branches",
            "muls", "divs", "fp_ops", "critical_path_cycles_x16", "loop_recurrence_cycles_x16",
            "bound_cycles_x16", "cbr_executed", "mispredicts"}
    require(set(features) == keys | {"bpred"} and all(type(features[k]) is int and
            0 <= features[k] < 2**64 for k in keys), "invalid feature counters")
    return production, features


def collect(args):
    corpus_path = args.corpus.resolve()
    root = corpus_path.parent
    protocol_path = root / "protocol.json"
    corpus = json.loads(corpus_path.read_bytes())
    protocol = json.loads(protocol_path.read_bytes())
    cases = validate_cases(corpus, protocol)
    out = args.out.resolve()
    require(os.sched_getaffinity(0) == {args.cpu}, "run the collector on a single pinned CPU")
    out.mkdir(exist_ok=True)
    require(not (out / "dataset.json").exists(), "use a fresh output directory")
    (out / "bin").mkdir()
    (out / "samples").mkdir()
    source = root / "production" / "collect.py"
    spec = importlib.util.spec_from_file_location("durante_record_reader", source)
    reader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reader)
    bundle = args.bundle.resolve()
    tools = {"launcher": bundle / "dynamorio/bin64/drrun", "meter": bundle / "lib/libdurante.so",
             "runtime": bundle / "dynamorio/lib64/release/libdynamorio.so",
             "probe": args.client.resolve(), "production_reader": source}
    hashes = {k: digest(v) for k, v in tools.items()}
    require(hashes["meter"] == protocol["meter_sha256"], "unexpected v10 production meter")
    rustc = shutil.which("rustc")
    version = subprocess.check_output([rustc, "--version"], text=True).strip()
    require(version == corpus["compiler_version"], "unexpected Rust compiler version")
    data = {"schema": 1, "complete": False, "cpu": args.cpu, "collector_pid": os.getpid(),
            "collector_sha256": digest(Path(__file__)), "protocol_sha256": digest(protocol_path),
            "corpus_sha256": digest(corpus_path), "compiler": {"executable": rustc, "version": version,
            "flags": corpus["compiler_flags"]}, "tools": hashes, "programs": {}, "samples": []}
    save(out / "dataset.json", data)
    binaries = compile_programs(corpus, root / "src", out, rustc, data)
    for rep, offset in enumerate(protocol["round_rotations"], start=1):
        for name, case in cases[offset:] + cases[:offset]:
            row = {"workload_id": name, "scale": case["scale"], "repetition": rep,
                   **data["programs"][name], "expected_sha256": case["expected_sha256"],
                   "input_sha256": case["input_sha256"]}
            folder = out / "samples" / f'{len(data["samples"])+1:02d}-{name}-{case["scale"]}-{rep}'
            folder.mkdir()
            (folder / "stdin").write_text(case["stdin"])
            for phase in ("native_before", "metered", "native_after"):
                run = folder / phase
                run.mkdir()
                binary = binaries[name]
                if phase == "metered":
                    (run / "records").mkdir()
                    command = metered_command(tools, run, binary)
                else:
                    command = [binary]
                result = run_phase(command, run, case["expected_sha256"], args.cpu)
                if phase == "metered":
                    result["production"], result["features"] = feature_result(run, result["pid"], reader.collect)
                row[phase] = result
                save(folder / "triplet.json", row)
            data["samples"].append(row)
            save(out / "dataset.json", data)
    require(all(digest(path) == data["programs"][name]["binary_sha256"] for name, path in binaries.items()),
            "binary changed during collection")
    require(all(digest(path) == hashes[name] for name, path in tools.items()), "tool changed during collection")
    require(len(data["samples"]) == len(cases) * protocol["repetitions"], "incomplete collection")
    data["complete"] = True
    save(out / "dataset.json", data)


def timed_out(signum, frame):
    raise TimeoutError("phase exceeded 180 seconds")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["collect"])
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--client", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--cpu", type=int, required=True)
    signal.signal(signal.SIGALRM, timed_out)
    collect(parser.parse_args())
