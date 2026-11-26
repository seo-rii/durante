"""Compare two released Durante clients and time matched Scrivener query orders on CPU 1."""
import argparse
import json
from pathlib import Path
import signal
import sys
import tempfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from cpp20_locality.verify import require, sha
from deadline_accuracy.gate import placement
from first_run_sampler.profile import elf_digest
from guarded_native.anchor import static_elf
from shape_calibration.bench import pinned_cpu

TOOL_PATHS = {
    "reader": "production_reader.py",
    "drrun": "dynamorio/bin64/drrun",
    "engine": "dynamorio/lib64/release/libdynamorio.so",
    "previous": "previous.so",
    "current": "current.so",
}


def schedule(plan):
    pairs = [(case["name"], program) for case in plan["cases"] for program in plan["binaries"]]
    pairs.sort(key=lambda pair: sha("/".join(pair).encode()))
    for rep in (1, 2):
        for index, (name, program) in enumerate(pairs):
            # Each pair sees both directions, with half the first round in either order.
            arms = ("previous", "current") if (index + rep) % 2 else ("current", "previous")
            for arm in arms:
                yield dict(name=name, program=program, arm=arm, rep=rep)
    for name, program in pairs:
        yield dict(name=name, program=program, arm="diagnostic", rep=1)
    for rep in range(1, 10):
        offset = (rep - 1) % len(pairs)
        for name, program in pairs[offset:] + pairs[:offset]:
            yield dict(name=name, program=program, arm="native", rep=rep)


def read_profile(records, pid, arm, reader):
    path = records / f"process-{pid}.jsonl"
    require(set(records.iterdir()) == {path}, "unexpected profile record set")
    report = reader(records, pid)
    require(report["root_pid"] == pid and report["complete"] and report["valid"]
            and report["process_count"] == report["image_count"] == report["segments"][0]["threads"] == 1
            and report["quality"] == "uninterrupted" and report["signals_observed"] == 0
            and report["processes"][0]["exit_code"] == 0, "incomplete process coverage")
    require(report["authority"] == "diagnostic_only" and report["integrity_verified"] is False
            and report["measurement_contract"]["trusted_verdict_eligible"] is False, "invalid counter authority")
    parts = report["rep_breakdown"]
    require(report["instructions"] > 0 and report["cpuid_profile"] == "x86-64-v2"
            and report["rep_telemetry_status"] == "movs_stos_only" and report["rep_bytes_enabled"] is True
            and report["unsupported_rep_entries"] == 0 and parts["version"] == 1
            and report["rep_bytes"] == parts["movs_bytes"] + parts["stos_bytes"], "incomplete work counters")
    full = arm == "diagnostic"
    require(("latency_bound" in report) == ("memory_operand_bytes" in report) == full, "wrong metering mode")
    if full:
        latency, memory = report["latency_bound"], report["memory_operand_bytes"]
        require(latency["version"] == memory["version"] == 1 and memory["bytes"] > 0
                and latency["cycles_x16"] >= 4 * report["instructions"], "invalid diagnostic counters")
    return elf_digest(path), report


def collect_run(payload, out):
    from scrivener_order import corpus

    raw = (HERE / "plan.json").read_bytes()
    plan = json.loads(raw)
    require(plan["schema"] == 1 and plan["authority"] == "diagnostic_only", "unrecognized registration")
    tools = {key: elf_digest(payload / path) for key, path in TOOL_PATHS.items()}
    require(tools == plan["tools"], "unexpected reader, engine or client build")
    require(set(plan["binaries"]) == {"scrivener", "scrivener_lifting"}, "unexpected programs")
    binaries = {name: payload / "binaries" / name for name in plan["binaries"]}
    for name, path in binaries.items():
        require(static_elf(path) == plan["binaries"][name], "different registered executable: " + name)
    cases = {row["name"]: row for row in plan["cases"]}
    require(len(cases) == len(plan["cases"]) and set(cases) == set(corpus.NAMES), "unexpected case registration")

    # The shared wait4 harness imports production_reader at module load. Load the
    # registered reader from this payload before importing the harness.
    sys.path.insert(0, str(payload))
    import production_reader
    from cpp20_locality.bench import execute, quoted, steal, timeout
    require(Path(production_reader.__file__).resolve() == (payload / TOOL_PATHS["reader"]).resolve(),
            "reader module does not match the verified payload")

    affinity, allowed = pinned_cpu()
    context = placement()
    require(context["cpu"] == 1 and context["machine"] == "x86_64", "unexpected processor")
    signal.signal(signal.SIGALRM, timeout)
    out.mkdir()
    (out / "runs").mkdir()
    rows = []
    with tempfile.TemporaryDirectory(dir=out) as temp:
        inputs = Path(temp)
        for name, case in cases.items():
            stdin, oracle = corpus.make(name)
            require(sha(stdin.encode()) == case["stdin_sha256"] and sha(oracle.encode()) == case["stdout_sha256"],
                    "registered input or oracle differs: " + name)
            (inputs / case["stdin_sha256"]).write_bytes(stdin.encode())
        start_steal = steal(1)
        for seq, slot in enumerate(schedule(plan)):
            name, program, arm = slot["name"], slot["program"], slot["arm"]
            case = cases[name]
            directory = out / "runs" / f"{seq:03d}"
            directory.mkdir()
            cmd = [binaries[program]]
            if arm != "native":
                records = directory / "records"
                records.mkdir()
                options = "-outdir " + quoted(records) + " -rep_bytes -cpuid_profile x86-64-v2"
                if arm == "diagnostic":
                    options += " -latency_bound -memory_operand_bytes"
                client = "previous" if arm == "previous" else "current"
                cmd = [payload / TOOL_PATHS["drrun"], "-quiet", "-client", payload / TOOL_PATHS[client],
                       "00000000", options, "-follow_children", "-disable_traces", "-vm_size", "32M",
                       "-vmheap_size", "32M", "--", binaries[program]]
            obs = execute(cmd, directory, inputs / case["stdin_sha256"], {"trial_timeout_s": 120})
            require(obs["stdout_sha256"] == case["stdout_sha256"] and obs["cpu_us"] > 0,
                    f"wrong output or missing process time at row {seq}")
            if arm != "native":
                obs["record_sha256"], _ = read_profile(records, obs["launcher_pid"], arm, production_reader.collect)
            (directory / "stdout").unlink()
            (directory / "stderr").unlink()
            rows.append(dict(**slot, sequence=seq, elf_sha256=plan["binaries"][program],
                             stdin_sha256=case["stdin_sha256"], **obs))
        steal_ticks = steal(1) - start_steal
    require(steal_ticks >= 0 and pinned_cpu() == (affinity, allowed) and placement() == context,
            "guest placement changed")
    result = dict(schema=1, authority="diagnostic_only", plan_sha256=sha(raw), tools=tools, placement=context,
                  cgroup_allowed_cpus=allowed, cpu_steal_ticks=steal_ticks, rows=rows)
    (out / "run.json").write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    collect_run(args.payload.resolve(), args.out.resolve())
