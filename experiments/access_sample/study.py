"""Common receipt and schedule checks for the sampled-load experiment."""
import json
from pathlib import Path
import runpy

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
common = runpy.run_path(str(HERE.parent / "dependency_pairs/study.py"))
require, sha, uint, digest, read_json = (common[k] for k in ("require", "sha", "uint", "digest", "read_json"))
TOOLS = {"launcher", "runtime", "meter", "sampler", "production_reader"}
METRICS = {"loads", "pairs", "near_pairs", "far_pairs"}


def load_plan(data):
    raw = (HERE / "protocol.json").read_bytes()
    require(data["protocol_sha256"] == sha(raw), "wrong frozen protocol")
    return json.loads(raw)


def schedule(plan):
    cases = {case["workload_id"]: (i, case) for i, case in enumerate(plan["cases"])}
    require(len(cases) == len(plan["cases"]) == 12 and len(plan["rounds"]) == 3, "expected three rounds of twelve")
    require(set(plan["tool_sha256"]) == TOOLS and all(digest(v) for v in plan["tool_sha256"].values()) and
            plan["sources_sha256"] and all(digest(v) for v in plan["sources_sha256"].values()), "invalid frozen hashes")
    for rep, names in enumerate(plan["rounds"]):
        require(len(names) == len(cases) and set(names) == set(cases), "wrong round membership")
        for name in names:
            i, case = cases[name]
            require(digest(case["binary_sha256"]) and sha(bytes.fromhex(case["stdin_hex"])) == case["input_sha256"] and
                    sha(bytes.fromhex(case["stdout_hex"])) == case["expected_sha256"], "invalid frozen fixture")
            modes = ["baseline", "sampled"] if (rep+i) % 2 == 0 else ["sampled", "baseline"]
            yield rep+1, case, ["native_before", *modes, "native_after"]


def validate_sample(sample, pid):
    require(isinstance(sample, dict) and set(sample) == METRICS | {"schema", "pid", "threads", "period", "line_bytes", "sites"},
            "wrong sampled receipt fields")
    require(all(uint(sample[k]) and sample[k] == v for k, v in
                (("schema", 1), ("pid", pid), ("threads", 1), ("period", 1024), ("line_bytes", 64))),
            "wrong sampler identity or configuration")
    require(all(uint(sample[k]) for k in METRICS) and isinstance(sample["sites"], list), "invalid sample totals")
    previous = -1
    totals = {k: 0 for k in METRICS}
    for site in sample["sites"]:
        require(isinstance(site, dict) and set(site) == METRICS | {"offset"} and
                all(uint(site[k]) for k in site) and site["offset"] > previous and site["loads"] > 0,
                "unordered, duplicate or invalid sampled site")
        previous = site["offset"]
        full, extra = divmod(site["loads"], 1024)
        require(site["pairs"] == full + (extra >= 2) == site["near_pairs"] + site["far_pairs"],
                "wrong pair cadence or classification totals")
        for k in totals:
            totals[k] += site[k]
    require(all(totals[k] == sample[k] for k in totals), "site counts do not reconcile")


def read_sample(raw):
    def reject_duplicates(pairs):
        out = dict(pairs)
        require(len(out) == len(pairs), "duplicate sample field")
        return out
    require(0 < len(raw) <= 4*1024*1024, "unexpected sample size")
    return json.loads(raw, object_pairs_hook=reject_duplicates)


def validate_production(p):
    require(p["complete"] is True and p["valid"] is True and p["quality"] == "uninterrupted" and
            p["cpuid_profile"] == "x86-64-v2" and all(uint(p[k]) and p[k] == 1 for k in
            ("process_count", "image_count", "threads")) and all(uint(p[k]) and p[k] == 0 for k in
            ("signals_observed", "unsupported_rep_entries")), "invalid production scope")
    require(uint(p["instructions"], 1) and all(uint(p[k]) for k in ("rep_bytes", "rep_movs_bytes", "rep_stos_bytes"))
            and p["rep_bytes"] == p["rep_movs_bytes"] + p["rep_stos_bytes"], "invalid instructions or REP bytes")
    for name, value in (("latency_bound", "cycles_x16"), ("memory_operand_bytes", "bytes")):
        metric = p[name]
        require(set(metric) == {"version", value} and uint(metric["version"]) and metric["version"] == 1 and
                uint(metric[value], 1), "invalid "+name)
    require(p["latency_bound"]["cycles_x16"] >= 4*p["instructions"], "latency below issue bound")


def validated_rows(data, plan):
    require(uint(data["schema"], 1) and data["schema"] == 1 and data["complete"] is True and
            uint(data["cpu"], 1) and data["cpu"] == 1 and uint(data["collector_pid"], 1) and
            len(data["samples"]) == 36, "expected 36 completed CPU 1 quartets")
    require(data["tools"] == plan["tool_sha256"] and data["sources_sha256"] == plan["sources_sha256"] and
            data["collector_sha256"] == plan["sources_sha256"]["experiments/access_sample/collect.py"], "mismatched tools")
    programs = {c["workload_id"]: {"binary_sha256": c["binary_sha256"]} for c in plan["cases"]}
    require(data["programs"] == programs, "mismatched executable identities")
    pids = {data["collector_pid"]}
    for number, ((rep, case, phases), row) in enumerate(zip(schedule(plan), data["samples"], strict=True), 1):
        require(uint(row["repetition"]) and row["repetition"] == rep and row["phase_order"] == phases and
                all(row[k] == case[k] for k in ("workload_id", "binary_sha256", "input_sha256", "expected_sha256")),
                "schedule or fixture mismatch")
        for phase in phases:
            obs = row[phase]
            require(uint(obs["pid"], 1) and obs["pid"] not in pids and uint(obs["exit_code"]) and obs["exit_code"] == 0
                    and obs["stdout_sha256"] == case["expected_sha256"], "invalid child identity, exit or stdout")
            pids.add(obs["pid"])
            require(uint(obs["wall_ns"], 1) and uint(obs["cpu_time_ns"], 1) and uint(obs["user_ns"]) and
                    uint(obs["system_ns"]) and obs["cpu_time_ns"] == obs["user_ns"] + obs["system_ns"],
                    "invalid wait4 accounting")
            require(("production" in obs) == (phase in ("baseline", "sampled")) and
                    ("sample" in obs) == ("sample_sha256" in obs) == (phase == "sampled"), "wrong mode receipt")
            if "production" in obs:
                validate_production(obs["production"])
            if phase == "sampled":
                require(digest(obs["sample_sha256"]), "invalid sample digest")
                validate_sample(obs["sample"], obs["pid"])
        yield number, row
