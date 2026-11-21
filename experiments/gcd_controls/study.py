"""Shared registration, counterbalanced schedule and canonical receipt checks."""
import hashlib
import json
from pathlib import Path
import runpy

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PLAN = HERE / "protocol.json"
prior = runpy.run_path(str(ROOT / "experiments/rust_operand_bytes/collect.py"))
shared = prior["shared"]
production = runpy.run_path(str(ROOT / "experiments/divide_clock/fast_reader.py"))["production"]
require, sha = shared.require, shared.sha
MODES = ("fast", "trips")
validate_trip = runpy.run_path(str(HERE / "trip_probe/receipt.py"))["validate"]
CASE_KEYS = ("workload_id", "binary_sha256", "input_sha256", "expected_sha256")


def plan():
    value = json.loads(PLAN.read_bytes())
    ids = json.loads((HERE / "evaluation-plan.json").read_bytes())["cases"]
    require(value["schema"] == 1 and value["rounds"] == 4 and value["metered_phases"] == list(MODES)
            and [c["workload_id"] for c in value["cases"]] == ids
            and len(set(ids)) == len(ids) == 10, "unexpected registered design")
    require(all(shared.digest(ROOT/key) == expected for key, expected in value["sources_sha256"].items()),
            "registered source or fixture manifest changed")
    for case in value["cases"]:
        require(sha(bytes.fromhex(case["stdout_hex"])) == case["expected_sha256"]
                and case["input_size"] >= 0 and case["input_file"] == Path(case["input_file"]).name,
                "invalid fixture")
    return value


def schedule(value):
    cases = value["cases"]
    for index in range(value["rounds"]):
        offset = (index // 2) * (len(cases) // 2)
        ordered = cases[offset:] + cases[:offset]
        for case in (reversed(ordered) if index % 2 else ordered):
            modes = MODES if (index + cases.index(case)) % 2 == 0 else MODES[::-1]
            yield index + 1, case, ["native_before", *modes, "native_after"]


def input_manifest(value):
    files = {}
    for case in value["cases"]:
        entry = {"length": case["input_size"], "sha256": case["input_sha256"]}
        require(files.setdefault(case["input_file"], entry) == entry, "inconsistent shared input")
    return files


def read_inputs(directory, expected):
    """Read every regular file sequentially in full, also warming its page-cache contents."""
    observed = {}
    for name in sorted(expected):
        path = directory / name
        require(path.is_file() and not path.is_symlink(), "registered input must be a regular file")
        digest = hashlib.sha256()
        length = 0
        with path.open("rb") as source:
            require(source.seekable(), "stdin must be seekable")
            for block in iter(lambda: source.read(1024 * 1024), b""):
                length += len(block)
                digest.update(block)
        observed[name] = {"length": length, "sha256": digest.hexdigest()}
    require(observed == expected, "input bytes differ from registration")
    return observed


def rows(data):
    value = plan()
    require(data["schema"] == 1 and data["complete"] is True and data["cpu"] == 1
            and type(data["collector_pid"]) is int and data["collector_pid"] > 0
            and len(data["samples"]) == len(value["cases"]) * value["rounds"]
            and data["protocol_sha256"] == shared.digest(PLAN)
            and data["tools"] == value["tool_sha256"] and data["sources_sha256"] == value["sources_sha256"]
            and data["collector_sha256"] == shared.digest(HERE / "collect.py")
            and data["inputs_before"] == data["inputs_after"] == input_manifest(value), "incomplete study")
    pids = {data["collector_pid"]}
    for (rep, case, phases), row in zip(schedule(value), data["samples"], strict=True):
        require(row["phase_order"] == phases and row["repetition"] == rep
                and all(row[k] == case[k] for k in CASE_KEYS), "mismatched workload or phase order")
        for phase in phases:
            obs = row[phase]
            require(type(obs["pid"]) is int and obs["pid"] > 0 and obs["pid"] not in pids
                    and obs["exit_code"] == 0 and obs["stdout_sha256"] == case["expected_sha256"]
                    and obs["cpu_time_ns"] == obs["user_ns"] + obs["system_ns"] > 0
                    and min(obs["user_ns"], obs["system_ns"]) >= 0 and obs["wall_ns"] > 0,
                    "invalid child receipt")
            pids.add(obs["pid"])
            require(("production" in obs) == (phase in MODES) and ("trip" in obs) ==
                    ("trip_sha256" in obs) == (phase == "trips"), "invalid phase fields")
            if phase in MODES:
                require(obs["production"]["latency_bound"]["version"] == 9001, "unexpected meter")
            if phase == "trips":
                validate_trip(obs["trip"], obs["pid"])
        yield row, case, phases
