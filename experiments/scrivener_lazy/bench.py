"""Measure paid eager and demand-driven Scrivener deadline decisions before revealing native labels."""
import argparse
import json
import os
from pathlib import Path
import signal
from statistics import median
import sys
from tempfile import TemporaryDirectory
from time import monotonic_ns, thread_time_ns

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from cpp20_locality.verify import require, sha
from deadline_accuracy.gate import placement
from first_run_sampler.profile import elf_digest
from guarded_native.anchor import static_elf
from scrivener_cache.bench import CACHES, rows_digest, save_json
from scrivener_lazy import corpus, model
from scrivener_pages.bench import TOOL_PATHS, measured_features
from scrivener_streams.bench import STUDY as STREAM_STUDY
from shape_calibration.bench import pinned_cpu


def pairs(plan, group):
    return sorted(((case["name"], program) for case in plan["cases"] if case["group"] == group
                   for program in plan["binaries"]), key=lambda pair: sha("/".join(pair).encode()))


def native_schedule(plan, group, lane):
    ordered = pairs(plan, group)
    rows = []
    for repetition in range(plan["native_reps"]):
        offset = repetition % len(ordered)
        for name, program in ordered[offset:] + ordered[:offset]:
            rows.append(dict(lane=lane, name=name, program=program, arm="native", rep=repetition + 1))
    return rows


def calibration_schedule(plan):
    rows = [dict(lane="calibration", name=name, program="scan", arm="scan", rep=1) for name in plan["anchors"]]
    for name, program in pairs(plan, "anchor"):
        rows.append(dict(lane="calibration", name=name, program=program, arm="branch", rep=1))
    return rows + native_schedule(plan, "anchor", "calibration")


def calls_schedule(plan):
    limits = {c["name"]: c["fixed_limit_ms"] for c in plan["cases"] if c["group"] == "holdout"}
    ordered = pairs(plan, "holdout")
    calls = []
    for repetition in range(plan["profile_reps"]):
        for step in range(len(ordered)):
            index = (step + repetition) % len(ordered)
            name, program = ordered[index]
            lanes = ("eager", "lazy") if (index + repetition) % 2 == 0 else ("lazy", "eager")
            for lane in lanes:
                calls.append(dict(lane=lane, name=name, program=program, rep=repetition + 1,
                                  limit_ms=limits[name]))
    return calls


def validation_schedule(plan):
    return native_schedule(plan, "holdout", "validation")


def anchor_summary(plan, rows):
    anchors = {program: {} for program in plan["binaries"]}
    for name in plan["anchors"]:
        scan, = (r for r in rows if r["arm"] == "scan" and r["name"] == name)
        for program, endpoints in anchors.items():
            pair = [r for r in rows if r["name"] == name and r["program"] == program]
            branch, = (r for r in pair if r["arm"] == "branch")
            samples = [r["cpu_us"] * 1000 for r in pair if r["arm"] == "native"]
            require(len(samples) == plan["native_reps"], "incomplete native anchor")
            endpoints[name] = dict(native_ns=median(samples), disruptive=scan["features"]["disruptive"],
                                   bcm=branch["features"]["bcm"])
    return anchors


def measure(payload, out, inputs, plan, case, slot, sequence, execute):
    program, arm = slot["program"], slot["arm"]
    directory = out / "runs" / f"{sequence:03d}"
    directory.mkdir()
    target = payload / "binaries" / program
    if arm == "branch":
        cmd = [payload / TOOL_PATHS["valgrind"], "--tool=cachegrind", "--cache-sim=no", "--branch-sim=yes",
               *(f"--{k}={v},8,64" for k, v in CACHES.items()), f"--cachegrind-out-file={directory / 'cachegrind.out'}",
               f"--log-file={directory / 'cachegrind.log'}", "--", target]
    elif arm == "scan":
        cmd = [payload / TOOL_PATHS["scan"]]
    else:
        require(arm == "native", "unknown measurement arm")
        cmd = [target]
    row = execute(cmd, directory, inputs / case["stdin_sha256"], {"trial_timeout_s": plan["timeout_s"]})
    expected = case["scan_sha256"] if arm == "scan" else case["stdout_sha256"]
    require(row["stdout_sha256"] == expected and row["cpu_us"] > 0, f"wrong output or CPU time at {sequence}")
    if arm == "scan":
        (directory / "stdout").rename(directory / "scan.out")
    else:
        (directory / "stdout").unlink()
    (directory / "stderr").unlink()
    if arm != "native":
        row["features"] = measured_features(arm, directory, row["launcher_pid"], program, None, STREAM_STUDY)
    receipts = {p.name: elf_digest(p) for p in sorted(directory.iterdir())}
    return dict(**slot, sequence=sequence, stdin_sha256=case["stdin_sha256"],
                elf_sha256=plan["tools"]["scan"] if arm == "scan" else plan["binaries"][program], receipts=receipts, **row)


def measured_call(spec, anchors, record, rows):
    slot = {key: spec[key] for key in ("lane", "name", "program", "rep")}
    start_sequence = len(rows)

    def scan():
        return record(dict(**slot, arm="scan"))["features"]["disruptive"]

    def branch():
        return record(dict(**slot, arm="branch"))["features"]["bcm"]

    start_wall = monotonic_ns()
    start_cpu = thread_time_ns()
    decision = model.decide(anchors, spec["limit_ms"] * 1_000_000, scan, branch, eager=spec["lane"] == "eager")
    router_cpu = thread_time_ns() - start_cpu
    elapsed = monotonic_ns() - start_wall
    return dict(**spec, decision=decision, sequence_ids=list(range(start_sequence, len(rows))),
                router_cpu_ns=router_cpu, elapsed_ns=elapsed)


def collect(payload, out):
    raw = (HERE / "plan.json").read_bytes()
    plan = json.loads(raw)
    require(plan["schema"] == 1 and plan["authority"] == "diagnostic_only" and plan["cpu"] == 1 and
            plan["native_reps"] == 9 and plan["profile_reps"] == 2, "invalid diagnostic registration")
    require(plan["anchors"] == ["ascending", "shuffled"] and [c["name"] for c in plan["cases"]] == list(corpus.NAMES)
            and all(c["group"] == ("anchor" if c["name"] in plan["anchors"] else "holdout") for c in plan["cases"])
            and all(type(c["fixed_limit_ms"]) is int and c["fixed_limit_ms"] > 0 and
                    c["fixed_limit_ms"] in plan["fixed_limits_ms"] for c in plan["cases"] if c["group"] == "holdout"),
            "invalid corpus or fixed deadline")
    tools = {name: elf_digest(payload / path) for name, path in TOOL_PATHS.items()}
    require(tools == plan["tools"] and set(plan["binaries"]) == {"scrivener", "scrivener_lifting"} and
            all(static_elf(payload / "binaries" / p) == digest for p, digest in plan["binaries"].items()),
            "different tools or target executable")
    sys.path.insert(0, str(payload))
    import production_reader
    from cpp20_locality.bench import execute, steal, timeout
    require(Path(production_reader.__file__).resolve() == payload / TOOL_PATHS["reader"], "unexpected reader")
    affinity, allowed = pinned_cpu()
    host = placement()
    require(host["cpu"] == plan["cpu"] and host["machine"] == "x86_64", "unexpected CPU")
    signal.signal(signal.SIGALRM, timeout)
    os.environ["VALGRIND_LIB"] = str(payload / "cachegrind")
    out.mkdir()
    (out / "runs").mkdir()
    rows = []
    seal = out / "predictions.json"
    cases = {c["name"]: c for c in plan["cases"]}
    with TemporaryDirectory(dir=out) as temp:
        inputs = Path(temp)
        for name, case in cases.items():
            data, answer = corpus.make(name)
            require(sha(data.encode()) == case["stdin_sha256"] and sha(answer.encode()) == case["stdout_sha256"],
                    f"changed input or oracle for {name}")
            (inputs / case["stdin_sha256"]).write_bytes(data.encode())

        def record(slot):
            row = measure(payload, out, inputs, plan, cases[slot["name"]], slot, len(rows), execute)
            rows.append(row)
            return row

        start = steal(1)
        for slot in calibration_schedule(plan):
            record(slot)
        anchors = anchor_summary(plan, rows)
        calls = [measured_call(spec, anchors[spec["program"]], record, rows) for spec in calls_schedule(plan)]
        save_json(seal, dict(plan_sha256=sha(raw), boot_sha256=host["boot_sha256"],
                             prefix_sha256=rows_digest(rows), calls=calls))
        for slot in validation_schedule(plan):
            record(slot)
        ticks = steal(1) - start
    require(ticks >= 0 and pinned_cpu() == (affinity, allowed) and placement() == host, "guest placement changed")
    save_json(out / "run.json", dict(schema=1, authority="diagnostic_only", plan_sha256=sha(raw),
                                    tools=tools, placement=host, cgroup_allowed_cpus=allowed, cpu_steal_ticks=ticks,
                                    rows=rows, predictions_sha256=elf_digest(seal)))


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--payload", required=True, type=Path)
    cli.add_argument("--out", required=True, type=Path)
    args = cli.parse_args()
    collect(args.payload.resolve(), args.out.resolve())
