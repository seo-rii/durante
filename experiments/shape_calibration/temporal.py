"""Measure same-site short-term address reuse on a trusted single-threaded static executable."""
from fractions import Fraction
import json
from pathlib import Path
import runpy
import signal
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from cpp20_locality.verify import release_reader, verify_counters
from first_run_sampler.profile import MAX_RECEIPT_BYTES, MAX_SITES, digest, elf_digest, read_json, require, sha, uint
from guarded_native.anchor import PROTOCOL, static_elf
from guarded_native.live import verified_runtime
from shape_calibration.shape import MATERIAL_SHARE

PERIOD = 8191
WINDOW = 32
RADIUS = 64
MIN_WINDOWS = 64
MAX_CHANGE = Fraction(1, 8)
FIELDS = ("loads", "windows", "exact_hits", "radius_hits")
PROBE_SHA = "159ca08d50bb8cf20e88f86b2186af422e094e7c14ebe98a42bed7adc2d6b186"


def sample_sites(raw, pid):
    require(len(raw) <= MAX_RECEIPT_BYTES and type(pid) is int and 0 < pid < 2**32, "bad temporal report size or PID")
    value = read_json(raw)
    config = dict(schema=1, pid=pid, threads=1, period=PERIOD, line_bytes=64, window=WINDOW, radius_lines=RADIUS)
    require(isinstance(value, dict) and set(value) == config.keys() | set(FIELDS) | {"sites"}
            and all(uint(value[k]) and value[k] == expected for k, expected in config.items()), "invalid temporal identity")
    sites = value["sites"]
    require(isinstance(sites, list) and len(sites) <= MAX_SITES, "invalid temporal site count")
    previous = -1
    for site in sites:
        require(isinstance(site, dict) and set(site) == set(FIELDS) | {"offset"} and all(uint(v) for v in site.values()),
                "invalid temporal site fields")
        require(previous < site["offset"] and site["loads"] > 0 and
                site["windows"] == (site["loads"] + PERIOD - 1 - WINDOW) // PERIOD and
                site["exact_hits"] <= site["radius_hits"] <= site["windows"], "inconsistent temporal site counters")
        previous = site["offset"]
    require(all(uint(value[k]) and sum(s[k] for s in sites) == value[k] for k in FIELDS), "wrong temporal totals")
    return sites


def inspect_run(out, elf_sha, stdin_sha, stdout_sha, probe_sha=PROBE_SHA, *, metadata_filename="result.json", stdin_file=None):
    result = read_json((out / metadata_filename).read_bytes())
    if stdin_file is None:
        stdin_file = out / "stdin"
    require(result["schema"] == 1 and result["authority"] == "diagnostic_only"
            and result["probe_sha256"] == probe_sha == PROBE_SHA and digest(elf_sha) and result["elf_sha256"] == elf_sha
            and result["stdin_sha256"] == stdin_sha == elf_digest(stdin_file)
            and result["stdout_sha256"] == stdout_sha == elf_digest(out / "stdout")
            and (out / "stderr").stat().st_size == 0 and result["exit_code"] == 0,
            "temporal input, probe or output mismatch")
    require(all(uint(result[f]) for f in ("user_us", "sys_us", "cpu_ns"))
            and result["cpu_ns"] == 1000 * (result["user_us"] + result["sys_us"]) > 0, "invalid temporal CPU usage")
    pid = result["pid"]
    with (out / "temporal.json").open("rb") as f:
        sample = f.read(MAX_RECEIPT_BYTES + 1)
    sites = sample_sites(sample, pid)
    record = out / "records" / f"process-{pid}.jsonl"
    production = (out / "production.json").read_bytes()
    require(set(record.parent.iterdir()) == {record} and sha(production) == result["production_sha256"]
            and sha(sample) == result["sample_sha256"], "temporal receipt changed")
    plan = read_json(PROTOCOL.read_bytes())
    reader = release_reader(plan["tools_sha256"]["production_reader.py"])
    verify_counters(record.read_bytes(), read_json(production), (out / "clock.bin").read_bytes(),
                    "live", pid, plan["work_budget"], reader)
    return {"observation": result, "sites": sites}


def capture(payload, probe, elf, stdin, out):
    payload, probe, elf, stdin, out = (Path(p).resolve() for p in (payload, probe, elf, stdin, out))
    elf_sha = static_elf(elf)
    require(elf_digest(probe) == PROBE_SHA, "temporal probe digest mismatch")
    verified_runtime(payload)
    plan = read_json(PROTOCOL.read_bytes())
    for name in ("cost_bench.py", "base/bench.py"):
        require(elf_digest(payload / name) == plan["tools_sha256"][name], "unexpected capture harness")
    sys.path.insert(0, str(payload))
    try:
        runtime = runpy.run_path(str(payload / "cost_bench.py"))
    finally:
        sys.path.pop(0)
    out.mkdir(mode=0o700)
    input_copy = out / "stdin"
    input_copy.write_bytes(stdin.read_bytes())
    argv = runtime["command"]("live", elf, out)
    where = argv.index("-follow_children")
    argv[where:where] = ["-client", probe, "00000001", "-out " + runtime["quoted"](out / "temporal.json")]
    base = runtime["base"]
    old = signal.signal(signal.SIGALRM, base["timeout"])
    try:
        row = base["execute"](argv, out, input_copy, plan)
    finally:
        signal.signal(signal.SIGALRM, old)
    pid = row["launcher_pid"]
    runtime["finish_live"]("live", out, pid)
    require(elf_digest(elf) == elf_sha, "temporal executable changed")
    result = dict(schema=1, authority="diagnostic_only", pid=pid, exit_code=row["exit_code"],
                  user_us=row["user_us"], sys_us=row["sys_us"], cpu_ns=row["cpu_us"] * 1000, elf_sha256=elf_sha,
                  stdin_sha256=elf_digest(input_copy), stdout_sha256=row["stdout_sha256"], probe_sha256=PROBE_SHA,
                  sample_sha256=elf_digest(out / "temporal.json"), production_sha256=elf_digest(out / "production.json"))
    (out / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    inspect_run(out, elf_sha, result["stdin_sha256"], result["stdout_sha256"])
    return result


def compare(reference, current):
    a, b = reference["observation"], current["observation"]
    require(a["elf_sha256"] == b["elf_sha256"] and a["probe_sha256"] == b["probe_sha256"] == PROBE_SHA,
            "different temporal executable or probe")
    sites = [{s["offset"]: s for s in row["sites"]} for row in (reference, current)]
    totals = [sum(s["loads"] for s in row.values()) for row in sites]
    material = set()
    for row, total in zip(sites, totals):
        material.update(offset for offset, s in row.items() if s["loads"] >= MATERIAL_SHARE * total)
    reasons = set() if material and all(totals) else {"no_temporal_material"}
    details = []
    for offset in sorted(material):
        pair = [row.get(offset) for row in sites]
        detail = {"offset": offset, "windows": [s["windows"] if s else 0 for s in pair], "reasons": []}
        if min(detail["windows"]) < MIN_WINDOWS:
            detail["reasons"].append("insufficient_temporal_windows")
        else:
            for k in ("exact_hits", "radius_hits"):
                left, right = (Fraction(s[k], s["windows"]) for s in pair)
                delta = abs(right - left)
                detail[k + "_difference"] = str(delta)
                if delta > MAX_CHANGE:
                    detail["reasons"].append(k + "_changed")
        details.append(detail)
        reasons.update(detail["reasons"])
    return dict(schema=1, status="needs_native" if reasons else "reuse", reasons=sorted(reasons), material_sites=details,
                reference_sample_sha256=a["sample_sha256"], current_sample_sha256=b["sample_sha256"])
