"""Time the shared input scan and both Scrivener executables, then seal predictions before held-out native timing."""
import argparse
import json
import os
from pathlib import Path
import signal
from statistics import median
import sys
from tempfile import TemporaryDirectory

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from contest_branch.bench import read_branch_profile, schedule as paired_schedule
from cpp20_locality.verify import require, sha
from deadline_accuracy.gate import placement
from first_run_sampler.profile import elf_digest
from guarded_native.anchor import static_elf
from scrivener_cache.bench import CACHES, TOOL_PATHS as CACHE_TOOLS, check_cachegrind_log, rows_digest, save_json
from scrivener_order.bench import read_profile
from scrivener_pages import corpus, score
from shape_calibration.bench import pinned_cpu

TOOL_PATHS = {**CACHE_TOOLS, "scan": "binaries/scan"}


def schedule(plan):
    names = sorted(corpus.NAMES, key=lambda name: sha(name.encode()))
    for rep in range(plan["scan_reps"]):
        for name in names[rep:] + names[:rep]:
            yield dict(name=name, program="scan", arm="scan", rep=rep + 1)
    yield from paired_schedule(plan, ("ordinary", "branch"))


def predictions(plan, prefix):
    anchors = plan["anchors"]
    times, scans, features = {}, {}, {}
    for row in prefix:
        name, program, arm = (row[k] for k in ("name", "program", "arm"))
        if arm == "native":
            require(name in anchors, "held-out timing entered calibration")
            times.setdefault((name, program), []).append(row["cpu_us"] * 1000)
        elif row["rep"] == 1:
            if arm == "scan":
                scans[name] = row["features"]
            else:
                features.setdefault((name, program), {}).update(row["features"])
    results = {case["name"]: {} for case in plan["cases"] if case["group"] == "holdout"}
    for program in plan["binaries"]:
        observations = {n: {**scans[n], **features[n, program]} for n in corpus.NAMES}
        require(all(len(times[n, program]) == plan["native_reps"] for n in anchors), "incomplete anchors")
        low, high = anchors
        for name in results:
            results[name][program] = score.forecasts(median(times[low, program]), median(times[high, program]),
                                                     observations[low], observations[high], observations[name])
    return results


def check_inputs(plan):
    require(plan["anchors"] == ["ascending", "shuffled"] and
            [c["name"] for c in plan["cases"]] == list(corpus.NAMES), "unexpected corpus")
    for case in plan["cases"]:
        name = case["name"]
        text, answer = corpus.make(name)
        scan = "{accesses} {misses} {position_bits}\n".format(**corpus.features(text))
        require(case["group"] == ("anchor" if name in plan["anchors"] else "holdout") and
                all(sha(data.encode()) == case[key] for key, data in
                    (("stdin_sha256", text), ("stdout_sha256", answer), ("scan_sha256", scan))),
                f"changed registered input, solution oracle or scanner output for {name}")
        yield case, text


def measured_features(arm, directory, pid, program, reader):
    if arm == "ordinary":
        _, report = read_profile(directory / "records", pid, "current", reader)
        return {"work": report["instructions"] + report["rep_bytes"]}
    if arm == "branch":
        check_cachegrind_log(directory / "cachegrind.log", pid, program)
        return {"bcm": read_branch_profile(directory / "cachegrind.out", program)["Bcm"]}
    if arm == "scan":
        accesses, misses, bits = map(int, (directory / "scan.out").read_bytes().split())
        return dict(accesses=accesses, misses=misses, position_bits=bits)
    raise ValueError("unknown profile arm")


def collect(payload, out):
    plan_raw = (HERE / "plan.json").read_bytes()
    plan = json.loads(plan_raw)
    tools = {name: elf_digest(payload / path) for name, path in TOOL_PATHS.items()}
    require(tools == plan["tools"] and set(plan["binaries"]) == {"scrivener", "scrivener_lifting"} and
            all(static_elf(payload / "binaries" / p) == digest for p, digest in plan["binaries"].items()),
            "different tools or target executable")
    sys.path.insert(0, str(payload))
    import production_reader
    from cpp20_locality.bench import execute, quoted, steal, timeout
    require(Path(production_reader.__file__).resolve() == payload / TOOL_PATHS["reader"], "wrong reader")
    affinity, allowed = pinned_cpu()
    host = placement()
    require(host["cpu"] == 1 and host["machine"] == "x86_64", "unexpected CPU")
    signal.signal(signal.SIGALRM, timeout)
    os.environ["VALGRIND_LIB"] = str(payload / "cachegrind")
    out.mkdir()
    (out / "runs").mkdir()
    seal = out / "predictions.json"
    rows = []
    with TemporaryDirectory(dir=out) as temp:
        cases = {}
        for case, text in check_inputs(plan):
            cases[case["name"]] = case
            (Path(temp) / case["stdin_sha256"]).write_bytes(text.encode())
        start = steal(1)
        for seq, slot in enumerate(schedule(plan)):
            name, program, arm = (slot[k] for k in ("name", "program", "arm"))
            case = cases[name]
            if arm == "native" and case["group"] == "holdout" and not seal.exists():
                save_json(seal, dict(plan_sha256=sha(plan_raw), boot_sha256=host["boot_sha256"],
                                     prefix_sha256=rows_digest(rows), cases=predictions(plan, rows)))
            directory = out / "runs" / f"{seq:03d}"
            directory.mkdir()
            binary = payload / "binaries" / program
            cmd = [binary]
            if arm == "ordinary":
                records = directory / "records"
                records.mkdir()
                options = "-outdir " + quoted(records) + " -rep_bytes -cpuid_profile x86-64-v2"
                cmd = [payload / TOOL_PATHS["drrun"], "-quiet", "-client", payload / TOOL_PATHS["current"],
                       "00000000", options, "-follow_children", "-disable_traces", "-vm_size", "32M",
                       "-vmheap_size", "32M", "--", binary]
            elif arm == "branch":
                cmd = [payload / TOOL_PATHS["valgrind"], "--tool=cachegrind", "--cache-sim=no", "--branch-sim=yes",
                       *(f"--{k}={v},8,64" for k, v in CACHES.items()),
                       f"--cachegrind-out-file={directory / 'cachegrind.out'}",
                       f"--log-file={directory / 'cachegrind.log'}", "--", binary]
            row = execute(cmd, directory, Path(temp) / case["stdin_sha256"], {"trial_timeout_s": 120})
            expected = case["scan_sha256"] if arm == "scan" else case["stdout_sha256"]
            require(row["stdout_sha256"] == expected and row["cpu_us"] > 0, f"bad output or CPU time at {seq}")
            if arm == "scan":
                (directory / "stdout").rename(directory / "scan.out")
            else:
                (directory / "stdout").unlink()
            (directory / "stderr").unlink()
            if arm != "native":
                row["features"] = measured_features(arm, directory, row["launcher_pid"], program,
                                                    production_reader.collect)
            row["receipts"] = {f.relative_to(directory).as_posix(): elf_digest(f)
                               for f in sorted(directory.rglob("*")) if f.is_file()}
            rows.append(dict(**slot, sequence=seq, stdin_sha256=case["stdin_sha256"],
                             elf_sha256=tools["scan"] if arm == "scan" else plan["binaries"][program], **row))
        steal_ticks = steal(1) - start
    require(steal_ticks >= 0 and pinned_cpu() == (affinity, allowed) and placement() == host, "placement changed")
    save_json(out / "run.json", dict(schema=1, authority="diagnostic_only", plan_sha256=sha(plan_raw),
                                    tools=tools, placement=host, cgroup_allowed_cpus=allowed, cpu_steal_ticks=steal_ticks,
                                    rows=rows, predictions_sha256=elf_digest(seal)))


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--payload", required=True, type=Path)
    cli.add_argument("--out", required=True, type=Path)
    args = cli.parse_args()
    collect(args.payload.resolve(), args.out.resolve())
