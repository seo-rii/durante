"""Registration and schedule for the prospective GCD holdout."""
import argparse
import json
from pathlib import Path
import runpy

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PLAN = HERE / "protocol.json"
BASE = HERE.parent / "gcd_controls"
common = runpy.run_path(str(BASE / "study.py"))
CASE_KEYS, MODES, prior, production = (common[k] for k in ("CASE_KEYS", "MODES", "prior", "production"))
read_inputs, input_manifest = common["read_inputs"], common["input_manifest"]
require, shared, sha, validate_trip = (common[k] for k in ("require", "shared", "sha", "validate_trip"))
CASES = ("gcd_seed_a", "gcd_seed_b", "gcd_seed_c", "fibonacci_alternating", "fibonacci_runs")


def plan():
    value = json.loads(PLAN.read_bytes())
    require(value["schema"] == 1 and value["rounds"] == 4 and value["metered_phases"] == list(MODES)
            and tuple(c["workload_id"] for c in value["cases"]) == CASES, "unexpected prospective design")
    require(all(shared.digest(ROOT/name) == digest for name, digest in value["sources_sha256"].items()),
            "registered source or reference changed")
    for c in value["cases"]:
        raw = bytes.fromhex(c["stdin_hex"])
        require(sha(raw) == c["input_sha256"] and len(raw) == c["input_size"] and
                c["input_file"] == c["workload_id"]+".in" and
                sha(bytes.fromhex(c["stdout_hex"])) == c["expected_sha256"], "invalid input or oracle")
    return value


# The prospective rounds use precisely the first study's native bracketing and reversals.
schedule = common["schedule"]


def rows(data):
    value = plan()
    require(data["schema"] == 1 and data["complete"] is True and data["cpu"] == 1
            and type(data["collector_pid"]) is int and data["collector_pid"] > 0
            and len(data["samples"]) == value["rounds"] * len(value["cases"])
            and data["protocol_sha256"] == shared.digest(PLAN)
            and data["sources_sha256"] == value["sources_sha256"] and data["tools"] == value["tool_sha256"]
            and data["collector_sha256"] == shared.digest(HERE/"collect.py")
            and data["inputs_before"] == data["inputs_after"] == input_manifest(value), "incomplete holdout")
    pids = {data["collector_pid"]}
    for (rep, case, phases), row in zip(schedule(value), data["samples"], strict=True):
        require(row["repetition"] == rep and row["phase_order"] == phases and
                all(row[k] == case[k] for k in CASE_KEYS), "unregistered workload or phase order")
        for phase in phases:
            obs = row[phase]
            require(type(obs["pid"]) is int and obs["pid"] > 0 and obs["pid"] not in pids
                    and obs["exit_code"] == 0 and obs["stdout_sha256"] == case["expected_sha256"]
                    and obs["cpu_time_ns"] == obs["user_ns"] + obs["system_ns"] > 0
                    and min(obs["user_ns"], obs["system_ns"]) >= 0 and obs["wall_ns"] > 0, "invalid wait4")
            pids.add(obs["pid"])
            require(("production" in obs) == (phase in MODES) and
                    ("trip" in obs) == ("trip_sha256" in obs) == (phase == "trips"), "wrong diagnostic")
            if phase in MODES:
                require(obs["production"]["latency_bound"]["version"] == 9001, "wrong clock")
            if phase == "trips":
                validate_trip(obs["trip"], obs["pid"])
        yield row, case, phases


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description="Materialize and hash the five registered regular stdin files.")
    cli.add_argument("--write-inputs", required=True, type=Path)
    directory = cli.parse_args().write_inputs
    directory.mkdir()
    value = plan()
    for c in value["cases"]:
        dest = directory / c["input_file"]
        with dest.open("xb") as f:
            f.write(bytes.fromhex(c["stdin_hex"]))
        dest.chmod(0o444)
    observed = read_inputs(directory, input_manifest(value))
    print(f"Verified {len(observed)} distinct input files ({sum(v['length'] for v in observed.values())} bytes).")
