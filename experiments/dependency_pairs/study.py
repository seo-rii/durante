"""Common protocol and receipt checks for the pinned dependency-pair experiment."""
import gzip
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PHASES = ("native_before", "operand_bytes", "native_after")
TOOLS = {"launcher", "runtime", "meter", "production_reader"}


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read_json(path):
    raw = path.read_bytes()
    return json.loads(gzip.decompress(raw) if raw.startswith(b"\x1f\x8b") else raw)


def uint(value, minimum=0):
    return type(value) is int and minimum <= value < 2**64


def digest(value):
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def load_protocol(data):
    path = HERE / "protocol.json"
    raw = path.read_bytes()
    require(data["protocol_sha256"] == sha(raw), "wrong frozen protocol")
    return json.loads(raw)


def schedule(plan):
    cases = {c["workload_id"]: c for c in plan["cases"]}
    require(len(plan["cases"]) == len(cases) == 8 and len(plan["rounds"]) == 4, "expected four rounds of eight cases")
    require(set(plan["tool_sha256"]) == TOOLS and all(digest(v) for v in plan["tool_sha256"].values()),
            "invalid frozen tool set")
    require(plan["sources_sha256"] and all(digest(v) for v in plan["sources_sha256"].values()),
            "invalid frozen source digests")
    for case in cases.values():
        require(case["stdin_hex"] == "" and len(bytes.fromhex(case["stdout_hex"])) == 8 and
                sha(b"") == case["input_sha256"] and sha(bytes.fromhex(case["stdout_hex"])) == case["expected_sha256"]
                and digest(case["binary_sha256"]), "invalid fixture or oracle bytes")
    for repetition, names in enumerate(plan["rounds"], 1):
        require(len(names) == len(cases) and set(names) == set(cases), "wrong cases in round")
        for name in names:
            yield repetition, cases[name]


def validated_rows(data, plan):
    """Yield ordered validated triplets, each with its one-based archive sequence."""
    require(data["schema"] == 1 and data["complete"] is True and uint(data["cpu"], 1) and data["cpu"] == 1 and
            uint(data["collector_pid"], 1) and len(data["samples"]) == 32, "expected a complete CPU 1 measurement")
    require(data["tools"] == plan["tool_sha256"] and data["sources_sha256"] == plan["sources_sha256"] and
            data["collector_sha256"] == plan["sources_sha256"]["experiments/dependency_pairs/collect.py"],
            "tool or source set differs from protocol")
    programs = {c["workload_id"]: {"binary_sha256": c["binary_sha256"]} for c in plan["cases"]}
    require(data["programs"] == programs, "different measured binaries")
    pids = {data["collector_pid"]}
    for number, ((rep, case), row) in enumerate(zip(schedule(plan), data["samples"], strict=True), 1):
        require(uint(row["repetition"], 1) and row["repetition"] == rep and
                all(row[k] == case[k] for k in ("workload_id", "binary_sha256", "input_sha256", "expected_sha256"))
                and row["phase_order"] == list(PHASES), "schedule, case or binary mismatch")
        for phase in PHASES:
            obs = row[phase]
            require(uint(obs["pid"], 1) and obs["pid"] not in pids and uint(obs["exit_code"]) and
                    obs["exit_code"] == 0 and obs["stdout_sha256"] == case["expected_sha256"],
                    "invalid child PID, exit or output")
            pids.add(obs["pid"])
            require(uint(obs["wall_ns"], 1) and uint(obs["cpu_time_ns"], 1) and
                    uint(obs["user_ns"]) and uint(obs["system_ns"]) and
                    obs["cpu_time_ns"] == obs["user_ns"] + obs["system_ns"], "invalid final wait4 times")
            require(("production" in obs) == (phase == "operand_bytes"), "unexpected diagnostic receipt")
        p = row["operand_bytes"]["production"]
        require(p["complete"] is True and p["valid"] is True and p["quality"] == "uninterrupted" and
                p["cpuid_profile"] == "x86-64-v2" and all(uint(p[k]) and p[k] == 1 for k in
                ("process_count", "image_count", "threads")) and all(uint(p[k]) and p[k] == 0 for k in
                ("signals_observed", "unsupported_rep_entries")), "invalid production scope")
        require(uint(p["instructions"], 1) and all(uint(p[k]) for k in ("rep_bytes", "rep_movs_bytes", "rep_stos_bytes"))
                and p["rep_bytes"] == p["rep_movs_bytes"] + p["rep_stos_bytes"], "invalid instruction or REP counters")
        for name, value in (("latency_bound", "cycles_x16"), ("memory_operand_bytes", "bytes")):
            metric = p[name]
            require(set(metric) == {"version", value} and uint(metric["version"]) and metric["version"] == 1 and
                    uint(metric[value], 1), "malformed " + name)
        require(p["latency_bound"]["cycles_x16"] >= 4 * p["instructions"], "latency below issue bound")
        yield number, row
