"""Compare terminal instruction/REP counters and v11 live pages on the registered accuracy corpus."""
import argparse
from collections import defaultdict
from fractions import Fraction
import json
from pathlib import Path
from statistics import median
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from calibrated_limits.verify import METER, load, verify_placement
from cpp20_locality.export import parse_trace
from cpp20_locality.verify import release_reader, require, sha, verify_counters
from first_run_sampler.profile import elf_digest
from guarded_native.anchor import PROTOCOL
from shape_calibration.bench import pinned_cpu, write


def schedule(cases, rounds):
    ordered = sorted(cases, key=lambda c: sha(c["case_id"].encode()))
    for rnd in range(rounds):
        for pos, case in enumerate(ordered[rnd:] + ordered[:rnd]):
            clients = ("released", "candidate") if (pos + rnd) % 2 == 0 else ("candidate", "released")
            for client in clients:
                yield rnd, case, client


def collect(payload, binaries, out):
    plan = load(HERE / "plan.json")
    study = load(HERE / "client-parity.json")
    affinity, allowed = pinned_cpu()
    clients = {"released": payload / "v11.so", "candidate": payload.parent / "candidate.so"}
    hashes = {name: elf_digest(path) for name, path in clients.items()}
    require(hashes == study["client_sha256"], "different registered clients")
    sys.path.insert(0, str(payload))
    import cost_bench as runtime
    out.mkdir(mode=0o700)
    for sub in ("inputs", "runs"):
        (out / sub).mkdir()
    for case in plan["cases"]:
        require(elf_digest(binaries / case["program"]) == case["binary_sha256"], "different workload ELF")
        (out / "inputs" / sha(case["stdin"].encode())).write_bytes(case["stdin"].encode())
    rows = []
    start = runtime.base["steal"](1)
    for rnd, case, arm in schedule(plan["cases"], study["rounds"]):
        folder = out / "runs" / f"{len(rows):03d}"
        folder.mkdir()
        cmd = runtime.command("live", binaries / case["program"], folder)
        pos = cmd.index("-client") + 1
        require(cmd[pos] == clients["released"], "unexpected launch command")
        cmd[pos] = clients[arm]
        obs = runtime.base["execute"](cmd, folder, out / "inputs" / sha(case["stdin"].encode()),
                                     {"trial_timeout_s": 120, "poll_interval_ms": 5, "work_budget": 2**64})
        require(obs["stdout_sha256"] == sha(case["stdout"].encode()), "wrong target output")
        runtime.finish_live("live", folder, obs["launcher_pid"])
        rows.append(dict(sequence=len(rows), round=rnd, case_id=case["case_id"], arm=arm,
                         pid=obs["launcher_pid"], stdout_sha256=obs["stdout_sha256"]))
    write(out / "summary.json", dict(schema=1, affinity=affinity, cgroup_cpus=allowed, client_sha256=hashes,
          plan_sha256=elf_digest(HERE / "plan.json"), parity_sha256=elf_digest(HERE / "client-parity.json"),
          steal_delta=runtime.base["steal"](1) - start, rows=rows))


def verify(root):
    plan = load(HERE / "plan.json")
    study = load(HERE / "client-parity.json")
    s = load(root / "summary.json")
    require(s["schema"] == 1 and s["plan_sha256"] == elf_digest(HERE / "plan.json")
            and s["parity_sha256"] == elf_digest(HERE / "client-parity.json")
            and s["client_sha256"] == study["client_sha256"] and s["affinity"] == [1] and s["cgroup_cpus"] == "1",
            "different study or guest")
    reader = release_reader(load(PROTOCOL)["tools_sha256"]["production_reader.py"])
    pids = {}
    counts = defaultdict(lambda: {"released": [], "candidate": []})
    for seq, (row, (rnd, case, arm)) in enumerate(zip(s["rows"], schedule(plan["cases"], study["rounds"]), strict=True)):
        pid = row["pid"]
        require((row["sequence"], row["round"], row["case_id"], row["arm"]) == (seq, rnd, case["case_id"], arm)
                and type(pid) is int and pid > 0 and pid not in pids, "wrong run or PID")
        pids[pid] = METER
        folder = root / "runs" / f"{seq:03d}"
        require((root / "inputs" / sha(case["stdin"].encode())).read_bytes() == case["stdin"].encode()
                and (folder / "stdout").read_bytes() == case["stdout"].encode() and not (folder / "stderr").stat().st_size
                and row["stdout_sha256"] == sha(case["stdout"].encode()), "different input or output")
        record = load(folder / "production.json")
        events = folder / "records" / f"process-{pid}.jsonl"
        require(list(events.parent.iterdir()) == [events], "additional target processes")
        verify_counters(events.read_bytes(), record, (folder / "clock.bin").read_bytes(), "live", pid, 3*(2**64-1), reader)
        rep = record["rep_breakdown"]
        counts[case["case_id"]][arm].append((record["instructions"], rep["movs_bytes"], rep["stos_bytes"]))
    verify_placement(root, pids)
    trace, _ = parse_trace((root / "exec-trace").read_text())
    require(set(trace) == set(pids), "unexpected measured processes")
    rows = {}
    for case_id, values in counts.items():
        a, b = ([v[0] for v in values[k]] for k in ("released", "candidate"))
        rep = {v[1:] for samples in values.values() for v in samples}
        ref, new = median(a), median(b)
        ppm = Fraction(1_000_000 * (new-ref), ref)
        rows[case_id] = dict(counts=values, median_difference_ppm=round(float(ppm), 6),
                median_exact=ref == new, all_instruction_counts_equal=len(set(a+b)) == 1,
                rep_consistent=len(rep) == 1,
                within_tolerance=abs(new-ref) <= max(study["absolute_instruction_tolerance"],
                Fraction(study["median_tolerance_ppm"] * ref, 1_000_000)))
    return dict(schema=1, verified_pids=len(pids), steal_ticks=s["steal_delta"], cases=rows)


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--payload", type=Path)
    cli.add_argument("--binaries", type=Path)
    cli.add_argument("--out", type=Path, required=True)
    cli.add_argument("--verify", action="store_true")
    cli.add_argument("--metrics", type=Path)
    args = cli.parse_args()
    if args.verify:
        result = verify(args.out)
        if args.metrics:
            write(args.metrics, result)
        else:
            print(json.dumps(result, indent=2))
    else:
        if not args.payload or not args.binaries:
            cli.error("collection requires --payload and --binaries")
        collect(args.payload.resolve(), args.binaries.resolve(), args.out.resolve())
