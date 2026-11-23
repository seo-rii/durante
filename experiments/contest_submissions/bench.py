"""Seal timing predictions for alternate contest solutions before measuring their native CPU time."""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import signal
import sys
import tempfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from contest_accuracy import rice
from contest_scale import variants
from contest_submissions.score import predict
from contest_warm import scrivener
from cpp20_locality.verify import require, sha, verify_counters
from deadline_accuracy.gate import placement
from first_run_sampler.profile import elf_digest
from guarded_native.anchor import static_elf
from shape_calibration.bench import pinned_cpu


def schedule(plan):
    names = sorted((case["case_id"] for case in plan["cases"]), key=lambda name: sha(name.encode()))
    for index, name in enumerate(names):
        roles = ("reference", "submission") if index % 2 == 0 else ("submission", "reference")
        for role in roles:
            yield dict(case_id=name, role=role, kind="profile", rep=0)
    for role, count in (("reference", plan["reference_rounds"]), ("submission", plan["submission_rounds"])):
        for index in range(count):
            offset = index % len(names)
            for name in names[offset:] + names[:offset]:
                yield dict(case_id=name, role=role, kind="native", rep=index + 1)


def rows_digest(rows):
    return sha(json.dumps(rows, sort_keys=True, separators=(",", ":")).encode())


def counters(report):
    return dict(instructions=report["instructions"], rep_bytes=report["rep_bytes"],
                cycles_x16=report["latency_bound"]["cycles_x16"],
                memory_operand_bytes=report["memory_operand_bytes"]["bytes"])


def make_input(case):
    program, name = case["case_id"].split("/")
    require(program == case["program"], "inconsistent case id")
    if program == "rice":
        return rice.make(name)
    require(program == "scrivener", "unknown program")
    if name in scrivener.CASES:
        return scrivener.make(name)
    return variants.editor(name)


def save(path, obj):
    with path.open("x") as stream:
        json.dump(obj, stream, indent=2)
        stream.write("\n")


def run(payload, binaries, out):
    protocol = json.loads((HERE.parent / "guarded_sampler/protocol.json").read_bytes())
    for name, digest in protocol["tools_sha256"].items():
        if name.startswith("dynamorio/") or name in ("production_reader.py", "v11.so"):
            require(elf_digest(payload / name) == digest, "different runtime file: " + name)
    # The registered collector supplies the same wait4, timeout, and file handling for both roles.
    sys.path.insert(0, str(payload))
    from cpp20_locality.bench import execute, quoted, steal, timeout
    from production_reader import collect

    raw = (HERE / "plan.json").read_bytes()
    plan = json.loads(raw)
    affinity, allowed = pinned_cpu()
    context = placement()
    require(plan["cpu"] == context["cpu"] == 1 and context["machine"] == "x86_64", "unexpected CPU")
    cases = {case["case_id"]: case for case in plan["cases"]}
    require(len(cases) == len(plan["cases"]), "duplicate case id")
    for case in cases.values():
        for role, key in (("reference", "program"), ("submission", "submission")):
            require(static_elf(binaries / case[key]) == case[role + "_elf_sha256"], "registered ELF differs")
    signal.signal(signal.SIGALRM, timeout)
    out.mkdir()
    (out / "runs").mkdir()
    predictions = out / "predictions.json"
    profile, reference_ns = defaultdict(dict), defaultdict(list)
    rows = []
    with tempfile.TemporaryDirectory(dir=out) as temp:
        inputs = Path(temp)
        for case in cases.values():
            stdin, stdout = make_input(case)
            require(sha(stdin.encode()) == case["stdin_sha256"] and sha(stdout.encode()) == case["stdout_sha256"],
                    "different input or oracle for " + case["case_id"])
            (inputs / case["stdin_sha256"]).write_bytes(stdin.encode())
        start_steal = steal(plan["cpu"])
        for slot in schedule(plan):
            name, role = slot["case_id"], slot["role"]
            case = cases[name]
            if role == "submission" and slot["kind"] == "native" and not predictions.exists():
                estimates = {key: predict(profile[key]["reference"], profile[key]["submission"], reference_ns[key])
                             for key in cases}
                save(predictions, dict(schema=1, plan_sha256=sha(raw), boot_sha256=context["boot_sha256"],
                                       prior_rows_sha256=rows_digest(rows), predictions=estimates))
            seq = len(rows)
            directory = out / "runs" / f"{seq:03d}"
            directory.mkdir()
            program = case["program"] if role == "reference" else case["submission"]
            binary = binaries / program
            argv = [binary]
            if slot["kind"] == "profile":
                records = directory / "records"
                records.mkdir()
                options = "-outdir " + quoted(records) + " -rep_bytes -cpuid_profile x86-64-v2 -latency_bound -memory_operand_bytes"
                argv = [payload / "dynamorio/bin64/drrun", "-quiet", "-client", payload / "v11.so", "00000000",
                        options, "-follow_children", "-disable_traces", "-vm_size", "32M", "-vmheap_size", "32M", "--", binary]
            observed = execute(argv, directory, inputs / case["stdin_sha256"], protocol)
            require(observed["stdout_sha256"] == case["stdout_sha256"] and observed["cpu_us"] > 0, "wrong output or CPU time")
            (directory / "stdout").unlink()
            (directory / "stderr").unlink()
            if slot["kind"] == "profile":
                pid = observed["launcher_pid"]
                record = records / f"process-{pid}.jsonl"
                require(list(records.iterdir()) == [record], "unexpected profile records")
                process_raw = record.read_bytes()
                report = collect(records, pid)
                verify_counters(process_raw, report, None, "diag", pid, 0, collect)
                observed["record_sha256"] = sha(process_raw)
                profile[name][role] = counters(report)
            elif role == "reference":
                reference_ns[name].append(observed["cpu_us"] * 1000)
            rows.append(dict(**slot, sequence=seq, program=program, elf_sha256=case[role + "_elf_sha256"],
                             stdin_sha256=case["stdin_sha256"], **observed))
        steal_ticks = steal(plan["cpu"]) - start_steal
    require(pinned_cpu() == (affinity, allowed) and placement() == context, "guest placement changed")
    save(out / "run.json", dict(schema=1, plan_sha256=sha(raw), placement=context, cgroup_allowed_cpus=allowed,
                                cpu_steal_ticks=steal_ticks, rows=rows, predictions_sha256=elf_digest(predictions)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("payload", "binaries", "out"):
        parser.add_argument("--" + name, required=True, type=Path)
    args = parser.parse_args()
    run(args.payload.resolve(), args.binaries.resolve(), args.out.resolve())
