"""Frozen inputs, counterbalanced schedule and record projections for the fresh CPU study."""
import json
from pathlib import Path
import runpy
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PLAN = HERE / "protocol.json"
sys.path.insert(0, str(HERE.parent))
from replay import validate

prior = runpy.run_path(str(ROOT / "experiments/rust_operand_bytes/collect.py"))
shared = prior["shared"]
reader = runpy.run_path(str(ROOT / "collect.py"))["collect"]
require, sha = shared.require, shared.sha
MODES = ("baseline", "sampled", "fast")


def plan():
    value = json.loads(PLAN.read_bytes())
    require(value["schema"] == 1 and [c["workload_id"] for c in value["cases"]] ==
            ["div_large_serial", "gcd", "floor_groups", "batch_quotients"], "unexpected registered cases")
    require(all(shared.digest(ROOT/k) == digest for k, digest in value["sources_sha256"].items()),
            "registered source or fitting evidence changed")
    for c in value["cases"]:
        require(sha(bytes.fromhex(c["stdin_hex"])) == c["input_sha256"] and
                sha(bytes.fromhex(c["stdout_hex"])) == c["expected_sha256"], "invalid input or oracle")
    return value


def schedule(value):
    cases = value["cases"]
    for r in range(3):
        for i in range(len(cases)):
            index = (i+r) % len(cases)
            rotation = (index+r) % len(MODES)
            modes = list(MODES[rotation:]+MODES[:rotation])
            yield r+1, cases[index], ["native_before", *modes, "native_after"]


def production(mode, records, pid):
    if mode == "fast":
        from fast_reader import production as fast_production
        return fast_production(records, pid)
    return prior["production"](records, pid, "operand_bytes", reader)


def rows(data):
    value = plan()
    require(data["schema"] == 1 and data["complete"] is True and data["cpu"] == 1 and
            isinstance(data["collector_pid"], int) and data["collector_pid"] > 0 and
            len(data["samples"]) == 12 and data["protocol_sha256"] == shared.digest(PLAN) and
            data["tools"] == value["tool_sha256"] and data["sources_sha256"] == value["sources_sha256"] and
            data["collector_sha256"] == shared.digest(HERE/"collect.py"), "unregistered or incomplete study")
    pids = {data["collector_pid"]}
    for (rep, case, phases), row in zip(schedule(value), data["samples"], strict=True):
        require(row["phase_order"] == phases and row["repetition"] == rep and
                all(row[k] == case[k] for k in ("workload_id", "binary_sha256", "input_sha256", "expected_sha256")),
                "mismatched workload or order")
        for mode in phases:
            obs = row[mode]
            require(type(obs["pid"]) is int and obs["pid"] > 0 and obs["pid"] not in pids and
                    obs["exit_code"] == 0 and obs["stdout_sha256"] == case["expected_sha256"] and
                    obs["cpu_time_ns"] == obs["user_ns"] + obs["system_ns"] > 0 and
                    min(obs["user_ns"],obs["system_ns"]) >= 0 and obs["wall_ns"] > 0, "invalid phase receipt")
            pids.add(obs["pid"])
            require(("production" in obs) == (mode in MODES) and
                    ("sample" in obs) == ("sample_sha256" in obs) == (mode == "sampled"), "invalid phase fields")
            if mode in MODES:
                require(obs["production"]["latency_bound"]["version"] == (9001 if mode == "fast" else 1),
                        "wrong latency model identity")
            if mode == "sampled":
                validate(obs["sample"], obs["pid"])
        yield row, case, phases
