"""Measure branch-only and full simulation on the same native contest workload."""
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
from scrivener_cache.bench import CACHES, TOOL_PATHS, check_cachegrind_log, read_cachegrind, rows_digest, save_json
from scrivener_order.bench import read_profile
from shape_calibration.bench import pinned_cpu

PROFILE_ARMS = ("ordinary", "full", "branch_only")
BRANCH_EVENTS = ("Ir", "Bc", "Bcm", "Bi", "Bim")


def schedule(plan, profile_arms=PROFILE_ARMS):
    pairs = sorted(((case["name"], program) for case in plan["cases"] for program in plan["binaries"]),
                   key=lambda pair: sha("/".join(pair).encode()))
    require(plan["profile_reps"] == len(profile_arms), "expected one profiling round per arm")
    for rep in range(plan["profile_reps"]):
        # Each input sees each tool in each position once across the profiling rounds.
        for step in range(len(pairs)):
            index = (step + rep) % len(pairs)
            name, program = pairs[index]
            first = (index + rep) % len(profile_arms)
            for arm in profile_arms[first:] + profile_arms[:first]:
                yield dict(name=name, program=program, arm=arm, rep=rep + 1)
    require(plan["native_reps"] == 9, "expected nine native observations per case")
    for calibration in (True, False):
        phase = [pair for pair in pairs if (pair[0] in plan["anchors"]) == calibration]
        for rep in range(plan["native_reps"]):
            first = rep % len(phase)
            for name, program in phase[first:] + phase[:first]:
                yield dict(name=name, program=program, arm="native", rep=rep + 1)


def read_branch_profile(path, program):
    lines = path.read_text().splitlines()
    descriptions = [line.split() for line in lines if line.startswith("desc:")]
    require(descriptions == [["desc:", key, "cache:", str(size), "B,", "64", "B,", "8-way", "associative"]
                             for key, size in CACHES.items()], "wrong branch-only cache descriptors")
    commands = [line[5:] for line in lines if line.startswith("cmd: ")]
    require(len(commands) == 1 and Path(commands[0]).name == program, "wrong branch-only target")
    headers = [line.split()[1:] for line in lines if line.startswith("events:")]
    totals = [line.split()[1:] for line in lines if line.startswith("summary:")]
    require(headers == [list(BRANCH_EVENTS)] and len(totals) == 1 and len(totals[0]) == len(BRANCH_EVENTS)
            and lines[-1].startswith("summary:") and all(v.isascii() and v.isdecimal() for v in totals[0]),
            "wrong branch-only events or summary")
    result = dict(zip(BRANCH_EVENTS, map(int, totals[0]), strict=True))
    require(result["Ir"] > 0 and result["Ir"] >= result["Bc"] + result["Bi"] and result["Bc"] >= result["Bcm"]
            and result["Bi"] >= result["Bim"], "inconsistent branch-only events")
    return result


def collect_run(payload, out):
    from contest_branch import corpus, score

    collect_study(payload, out, HERE, corpus.make, score.predict)


def collect_study(payload, out, study, make, predict, *, profile_arms=PROFILE_ARMS, read_probe=None):
    raw = (study / "plan.json").read_bytes()
    plan = json.loads(raw)
    require(plan["schema"] == 1 and plan["authority"] == "diagnostic_only", "invalid study registration")
    tools = {key: elf_digest(payload / path) for key, path in TOOL_PATHS.items()}
    require(plan["tools"] == tools, "changed verified tool")
    binaries = {name: payload / "binaries" / name for name in plan["binaries"]}
    require(binaries and all(static_elf(binary) == plan["binaries"][name] for name, binary in binaries.items()),
            "changed benchmark program")
    if read_probe is not None:
        probe = plan["probe"]
        require(probe["path"] == "binaries/leaf_pages"
                and static_elf(payload / probe["path"]) == probe["sha256"], "changed leaf-pages probe")
    cases = {case["name"]: case for case in plan["cases"]}
    anchors = plan["anchors"]
    require(len(cases) == len(plan["cases"]) and len(anchors) == len(set(anchors)) == 2
            and set(anchors) < cases.keys(), "invalid registered inputs or anchors")
    sys.path.insert(0, str(payload))
    import production_reader
    from cpp20_locality.bench import execute, quoted, steal, timeout
    require(Path(production_reader.__file__).resolve() == payload / TOOL_PATHS["reader"], "different reader module")
    affinity, allowed = pinned_cpu()
    host = placement()
    require(host["cpu"] == 1 and host["machine"] == "x86_64", "wrong guest CPU")
    signal.signal(signal.SIGALRM, timeout)
    os.environ["VALGRIND_LIB"] = str(payload / "cachegrind")
    out.mkdir()
    (out / "runs").mkdir()
    rows = []
    evidence = defaultdict(dict)
    seal = out / "predictions.json"
    with TemporaryDirectory(dir=out) as temp:
        for name, case in cases.items():
            text, oracle = make(name)
            require(sha(text.encode()) == case["stdin_sha256"] and sha(oracle.encode()) == case["stdout_sha256"],
                    "changed input or oracle")
            (Path(temp) / case["stdin_sha256"]).write_bytes(text.encode())
        start_steal = steal(1)
        for seq, slot in enumerate(schedule(plan, profile_arms)):
            name, program, arm = slot["name"], slot["program"], slot["arm"]
            if arm == "native" and name not in anchors and not seal.exists():
                forecasts = {n: {p: predict(evidence[anchors[0], p], evidence[anchors[1], p], evidence[n, p])
                                 for p in binaries} for n in cases if n not in anchors}
                save_json(seal, dict(schema=1, plan_sha256=sha(raw), boot_sha256=host["boot_sha256"],
                                     prefix_sha256=rows_digest(rows), cases=forecasts))
            directory = out / "runs" / f"{seq:03d}"
            directory.mkdir()
            binary = binaries[program]
            expected_elf = plan["binaries"][program]
            expected_answer = cases[name]["stdout_sha256"]
            if arm == "ordinary":
                records = directory / "records"
                records.mkdir()
                options = "-outdir " + quoted(records) + " -rep_bytes -cpuid_profile x86-64-v2"
                cmd = [payload / TOOL_PATHS["drrun"], "-quiet", "-client", payload / TOOL_PATHS["current"], "00000000",
                       options, "-follow_children", "-disable_traces", "-vm_size", "32M", "-vmheap_size", "32M",
                       "--", binary]
            elif arm in ("branch_only", "full"):
                profile = directory / "cachegrind.out"
                log = directory / "cachegrind.log"
                mode = "--cache-sim=yes" if arm == "full" else "--cache-sim=no"
                cmd = [payload / TOOL_PATHS["valgrind"], "--tool=cachegrind", mode, "--branch-sim=yes",
                       *(f"--{k}={size},8,64" for k, size in CACHES.items()), f"--cachegrind-out-file={profile}",
                       f"--log-file={log}", "--", binary]
            elif arm == "leaf_pages":
                cmd = [payload / probe["path"]]
                expected_elf = probe["sha256"]
                expected_answer = cases[name]["probe_stdout_sha256"]
            else:
                require(arm == "native", "unknown study arm")
                cmd = [binary]
            case = cases[name]
            obs = execute(cmd, directory, Path(temp) / case["stdin_sha256"], {"trial_timeout_s": plan["timeout_s"]})
            require(obs["stdout_sha256"] == expected_answer and obs["cpu_us"] > 0, "wrong output or CPU time")
            current = evidence[name, program]
            if arm == "ordinary":
                obs["record_sha256"], report = read_profile(records, obs["launcher_pid"], "current", production_reader.collect)
                current.setdefault(arm, {k: report[k] for k in ("instructions", "rep_bytes")})
            elif arm in ("branch_only", "full"):
                check_cachegrind_log(log, obs["launcher_pid"], program)
                parse = read_cachegrind if arm == "full" else read_branch_profile
                current.setdefault(arm, parse(profile, program))
                obs.update(cachegrind_sha256=elf_digest(profile), cachegrind_log_sha256=elf_digest(log))
            elif arm == "leaf_pages":
                current.setdefault(arm, read_probe(directory / "stdout"))
            elif name in anchors:
                current.setdefault("native_ns", []).append(obs["cpu_us"] * 1000)
            if arm == "leaf_pages":
                (directory / "stdout").rename(directory / "leaf_pages.out")
            else:
                (directory / "stdout").unlink()
            (directory / "stderr").unlink()
            rows.append(dict(**slot, sequence=seq, elf_sha256=expected_elf,
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
