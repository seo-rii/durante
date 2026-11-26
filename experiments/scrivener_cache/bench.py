"""Collect CPU-pinned, input-matched Durante, Cachegrind and direct native executions."""
import argparse
from collections import defaultdict
import json
import os
from pathlib import Path
import signal
import sys
from tempfile import TemporaryDirectory

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from cpp20_locality.verify import require, sha
from deadline_accuracy.gate import placement
from first_run_sampler.profile import elf_digest
from guarded_native.anchor import static_elf
from scrivener_cache import corpus
from scrivener_cache.score import predict
from scrivener_order.bench import read_profile
from shape_calibration.bench import pinned_cpu

ENDPOINTS = ("ascending", "shuffled")
EVENTS = ("Ir", "I1mr", "ILmr", "Dr", "D1mr", "DLmr", "Dw", "D1mw", "DLmw", "Bc", "Bcm", "Bi", "Bim")
CACHES = {"I1": 32768, "D1": 32768, "LL": 524288}
TOOL_PATHS = {"reader": "production_reader.py", "drrun": "dynamorio/bin64/drrun",
              "engine": "dynamorio/lib64/release/libdynamorio.so", "current": "current.so",
              "valgrind": "cachegrind/valgrind.bin", "cachegrind": "cachegrind/cachegrind-amd64-linux",
              "preload": "cachegrind/vgpreload_core-amd64-linux.so"}


def schedule(plan):
    pairs = [(case["name"], program) for case in plan["cases"] for program in plan["binaries"]]
    pairs.sort(key=lambda pair: sha("/".join(pair).encode()))
    for index, (name, program) in enumerate(pairs):
        arms = ("ordinary", "cachegrind") if index % 2 == 0 else ("cachegrind", "ordinary")
        for arm in arms:
            yield dict(name=name, program=program, arm=arm, rep=1)
    for endpoints in (True, False):
        phase = [pair for pair in pairs if (pair[0] in ENDPOINTS) == endpoints]
        for i in range(9):
            offset = i % len(phase)
            for name, program in phase[offset:] + phase[:offset]:
                yield dict(name=name, program=program, arm="native", rep=i + 1)


def rows_digest(rows):
    return sha(json.dumps(rows, sort_keys=True, separators=(",", ":")).encode())


def read_cachegrind(path, program=None):
    lines = path.read_text().splitlines()
    descriptions = [line.split() for line in lines if line.startswith("desc:")]
    expected = [["desc:", key, "cache:", str(size), "B,", "64", "B,", "8-way", "associative"]
                for key, size in CACHES.items()]
    require(descriptions == expected, "unexpected simulated cache geometry")
    commands = [line[5:] for line in lines if line.startswith("cmd: ")]
    require(len(commands) == 1 and (program is None or Path(commands[0]).name == program),
            "unexpected Cachegrind target")
    headers = [line.split()[1:] for line in lines if line.startswith("events:")]
    totals = [line.split()[1:] for line in lines if line.startswith("summary:")]
    require(len(headers) == len(totals) == 1 and len(headers[0]) == len(EVENTS)
            and set(headers[0]) == set(EVENTS) and len(totals[0]) == len(EVENTS)
            and lines[-1].startswith("summary:") and all(v.isascii() and v.isdecimal() for v in totals[0]),
            "incomplete Cachegrind summary")
    result = dict(zip(headers[0], map(int, totals[0]), strict=True))
    for refs, l1, ll in (("Ir", "I1mr", "ILmr"), ("Dr", "D1mr", "DLmr"), ("Dw", "D1mw", "DLmw")):
        require(result[refs] > 0 and result[refs] >= result[l1] >= result[ll], "invalid simulated misses")
    require(result["Bc"] >= result["Bcm"] and result["Bi"] >= result["Bim"], "invalid simulated branch misses")
    return result


def check_cachegrind_log(path, pid, program):
    prefix = f"=={pid}== "
    lines = path.read_text().splitlines()
    require(any(line.startswith(prefix + "Using Valgrind-3.19.0 and LibVEX;") for line in lines)
            and any(line.startswith(prefix + "Command: ") and Path(line.split("Command: ", 1)[1]).name == program
                    for line in lines), "wrong Cachegrind version, PID or executable")


def save_json(path, data):
    with path.open("x") as sink:
        json.dump(data, sink, indent=2)
        sink.write("\n")


def collect_run(payload, out):
    raw = (HERE / "plan.json").read_bytes()
    plan = json.loads(raw)
    require(plan["schema"] == 1 and plan["authority"] == "diagnostic_only", "invalid study registration")
    tools = {key: elf_digest(payload / path) for key, path in TOOL_PATHS.items()}
    require(plan["tools"] == tools, "changed verified tool")
    binaries = {name: payload / "binaries" / name for name in plan["binaries"]}
    require(set(binaries) == {"scrivener", "scrivener_lifting"}
            and all(static_elf(binary) == plan["binaries"][name] for name, binary in binaries.items()),
            "changed benchmark program")
    cases = {case["name"]: case for case in plan["cases"]}
    require(len(cases) == len(plan["cases"]) and set(cases) == set(corpus.NAMES), "invalid registered inputs")
    sys.path.insert(0, str(payload))
    import production_reader
    from cpp20_locality.bench import execute, quoted, steal, timeout
    require(Path(production_reader.__file__).resolve() == payload / TOOL_PATHS["reader"], "different reader module")
    affinity, allowed = pinned_cpu()
    host = placement()
    require(host["cpu"] == 1 and host["machine"] == "x86_64", "wrong guest CPU")
    signal.signal(signal.SIGALRM, timeout)
    # valgrind.bin uses this directory to exec the registered tool; all children inherit the same environment.
    os.environ["VALGRIND_LIB"] = str(payload / "cachegrind")
    out.mkdir()
    (out / "runs").mkdir()
    rows = []
    evidence = defaultdict(dict)
    seal = out / "predictions.json"
    with TemporaryDirectory(dir=out) as temp:
        for name, case in cases.items():
            text, oracle = corpus.make(name)
            require(sha(text.encode()) == case["stdin_sha256"] and sha(oracle.encode()) == case["stdout_sha256"],
                    "changed input or oracle")
            (Path(temp) / case["stdin_sha256"]).write_bytes(text.encode())
            bits = corpus.position_bits(text)
            for program in binaries:
                evidence[name, program]["position_bits"] = bits
        start_steal = steal(1)
        for seq, slot in enumerate(schedule(plan)):
            name, program, arm = slot["name"], slot["program"], slot["arm"]
            if arm == "native" and name not in ENDPOINTS and not seal.exists():
                predictions = {holdout: {p: predict(evidence["ascending", p], evidence["shuffled", p],
                                                     evidence[holdout, p]) for p in binaries}
                               for holdout in cases if holdout not in ENDPOINTS}
                save_json(seal, dict(schema=1, plan_sha256=sha(raw), boot_sha256=host["boot_sha256"],
                                     prefix_sha256=rows_digest(rows), cases=predictions))
            case = cases[name]
            directory = out / "runs" / f"{seq:03d}"
            directory.mkdir()
            cmd = [binaries[program]]
            if arm == "ordinary":
                records = directory / "records"
                records.mkdir()
                options = "-outdir " + quoted(records) + " -rep_bytes -cpuid_profile x86-64-v2"
                cmd = [payload / TOOL_PATHS["drrun"], "-quiet", "-client", payload / TOOL_PATHS["current"], "00000000",
                       options, "-follow_children", "-disable_traces", "-vm_size", "32M", "-vmheap_size", "32M",
                       "--", binaries[program]]
            elif arm == "cachegrind":
                profile = directory / "cachegrind.out"
                log = directory / "cachegrind.log"
                cmd = [payload / TOOL_PATHS["valgrind"], "--tool=cachegrind", "--cache-sim=yes", "--branch-sim=yes",
                       *(f"--{k}={size},8,64" for k, size in CACHES.items()), f"--cachegrind-out-file={profile}",
                       f"--log-file={log}", "--", binaries[program]]
            obs = execute(cmd, directory, Path(temp) / case["stdin_sha256"], {"trial_timeout_s": 120})
            require(obs["stdout_sha256"] == case["stdout_sha256"] and obs["cpu_us"] > 0, "wrong output or CPU time")
            current = evidence[name, program]
            if arm == "ordinary":
                obs["record_sha256"], report = read_profile(records, obs["launcher_pid"], "current", production_reader.collect)
                current["ordinary"] = {k: report[k] for k in ("instructions", "rep_bytes")}
            elif arm == "cachegrind":
                check_cachegrind_log(log, obs["launcher_pid"], program)
                current["cachegrind"] = read_cachegrind(profile, program)
                obs.update(cachegrind_sha256=elf_digest(profile), cachegrind_log_sha256=elf_digest(log))
            elif name in ENDPOINTS:
                current.setdefault("native_ns", []).append(obs["cpu_us"] * 1000)
            (directory / "stdout").unlink()
            (directory / "stderr").unlink()
            rows.append(dict(**slot, sequence=seq, elf_sha256=plan["binaries"][program],
                             stdin_sha256=case["stdin_sha256"], **obs))
        steal_ticks = steal(1) - start_steal
    require(steal_ticks >= 0 and pinned_cpu() == (affinity, allowed) and placement() == host,
            "guest placement changed")
    save_json(out / "run.json", dict(schema=1, authority="diagnostic_only", plan_sha256=sha(raw), tools=tools,
                                    placement=host, cgroup_allowed_cpus=allowed, cpu_steal_ticks=steal_ticks,
                                    rows=rows, predictions_sha256=elf_digest(seal)))


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--payload", required=True, type=Path)
    cli.add_argument("--out", required=True, type=Path)
    args = cli.parse_args()
    collect_run(args.payload.resolve(), args.out.resolve())
