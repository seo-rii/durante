"""Compare complete uncached, cold and warm preparation and fresh exact-input live enforcement."""
import argparse
import json
from pathlib import Path
import platform
import resource
import sys
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from cpp20_locality.verify import require, sha
from first_run_sampler.profile import elf_digest
from guarded_native import anchor, live
from preflight_cache import session
from shape_calibration import combined, run, select_policy
from shape_calibration.bench import pinned_cpu, write


def charge():
    # Linux rusage has microsecond user/system resolution. Capture absolute
    # readings so an independent reader can check monotonicity between phases.
    result = {"wall_ns": time.monotonic_ns()}
    for label, who in (("self", resource.RUSAGE_SELF), ("children", resource.RUSAGE_CHILDREN)):
        r = resource.getrusage(who)
        result[label + "_user_us"] = round(r.ru_utime * 1_000_000)
        result[label + "_sys_us"] = round(r.ru_stime * 1_000_000)
    return result


def timed(fn, *args):
    before = charge()
    result = fn(*args)
    return result, {"before": before, "after": charge()}


def fresh(payload, probe, elf, stdin, capture, refs, method):
    combined.capture(payload, probe, elf, stdin, capture)
    selected = select_policy.resolve(elf, capture, refs, elf_digest(probe), method)
    path = capture / "selection.json"
    anchor.save_new(path, selected)
    policy_dir = capture if selected["action"] == "calibrate" else refs[selected["reference_index"]]
    return dict(hit=False, key=None, capture=capture, selection=path, policy=policy_dir / "policy.json")


def paths(result, out):
    return {**{k: result[k] for k in ("key", "hit")},
            **{k: str(result[k].relative_to(out)) for k in ("capture", "policy", "selection")}}


def bench(payload, binaries, probe, out):
    raw = (HERE / "plan.json").read_bytes()
    plan = json.loads(raw)
    affinity, allowed = pinned_cpu()
    require(elf_digest(probe) == plan["probe_sha256"], "unregistered probe")
    sys.path.insert(0, str(payload))
    import cost_bench as runtime
    start = runtime.base["steal"](1)
    out.mkdir(mode=0o700)
    cache = out / "cache"
    cache.mkdir(mode=0o700)
    for sub in ("inputs", "trials"):
        (out / sub).mkdir()
    cases = {case["case_id"]: case for case in plan["cases"]}
    for case in cases.values():
        require(elf_digest(binaries / case["program"]) == case["binary_sha256"], "workload ELF differs")
        stdin = case["stdin"].encode()
        (out / "inputs" / sha(stdin)).write_bytes(stdin)

    def inputs(name):
        case = cases[name]
        return binaries / case["program"], out / "inputs" / sha(case["stdin"].encode())

    seeds = {}
    refs = {}
    for program, name in plan["anchors"].items():
        elf, stdin = inputs(name)
        seed, timing = timed(session.prepare, payload, probe, elf, stdin, cache, [], plan["method"])
        seeds[program] = dict(case_id=name, prepared=paths(seed, out), timing=timing)
        refs[program] = [seed["capture"]]

    rows = []
    queries = plan["queries"]
    for rnd in range(plan["rounds"]):
        for step in range(len(queries)):
            index = (rnd + step) % len(queries)
            query = queries[index]
            name = query["case_id"]
            elf, stdin = inputs(name)
            bank = refs[cases[name]["program"]]
            methods = ("fresh", "session") if (rnd + index) % 2 == 0 else ("session", "fresh")
            for mode in methods:
                sequence = len(rows)
                folder = out / "trials" / f"{sequence:03d}"
                folder.mkdir()
                if mode == "fresh":
                    prepared, timing = timed(fresh, payload, probe, elf, stdin, folder / "capture", bank, plan["method"])
                else:
                    prepared, timing = timed(session.prepare, payload, probe, elf, stdin, cache, bank, plan["method"])
                events = []
                for j, ms in enumerate(query["limits_ms"]):
                    result, cost = timed(run.run, payload, prepared["selection"], prepared["capture"],
                                         prepared["policy"], elf, stdin, folder / f"limit-{j}", ms,
                                         plan["timeout_ms"], plan["poll_ms"])
                    require(result["status"] in ("within_budget", "budget_exceeded"),
                            "missing real deadline decision: " + str(result.get("reason")))
                    events.append(dict(limit_ms=ms, exit_code=live.EXIT[result["status"]], timing=cost))
                rows.append(dict(sequence=sequence, round=rnd, case_id=name, arm=mode,
                                 prepared=paths(prepared, out), timing=timing, limits=events))
    write(out / "summary.json", dict(schema=1, plan_sha256=sha(raw), probe_sha256=elf_digest(probe),
          affinity=affinity, cgroup_cpus=allowed, kernel=platform.release(), seeds=seeds, rows=rows,
          steal_delta=runtime.base["steal"](1)-start))


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    for arg in ("payload", "probe", "binaries", "out"):
        cli.add_argument("--" + arg, type=Path, required=True)
    args = cli.parse_args()
    bench(args.payload.resolve(), args.binaries.resolve(), args.probe.resolve(), args.out.resolve())
