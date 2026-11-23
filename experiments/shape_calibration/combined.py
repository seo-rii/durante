"""Collect release v11, the full spatial sampler and the temporal sampler in one trusted execution."""
from pathlib import Path
import runpy
import signal
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from first_run_sampler.profile import elf_digest, read_json, require
from guarded_native.anchor import PROTOCOL, save_new, static_elf
from guarded_native.live import verified_runtime
from shape_calibration import shape, temporal


def _receipts(root, elf_sha, stdin_sha, stdout_sha, probe_sha):
    full = root / "full"
    files = full / "full"
    spatial = shape.inspect_run(full, elf_sha)
    temporal_report = temporal.inspect_run(files, elf_sha, stdin_sha, stdout_sha, probe_sha,
                                          metadata_filename="temporal-result.json", stdin_file=full / "stdin")
    obs = spatial["observation"]
    timing = temporal_report["observation"]
    release = read_json((full / "result.json").read_bytes())
    plan = read_json(PROTOCOL.read_bytes())
    clients = {name: plan["tools_sha256"][name] for name in ("v11.so", "span.so")}
    clients["temporal.so"] = probe_sha
    require(obs["launch_count"] == 1 and obs["pid"] == timing["pid"] and obs["stdin_sha256"] == stdin_sha
            and obs["stdout_sha256"] == stdout_sha and obs["production_sha256"] == timing["production_sha256"],
            "spatial and temporal receipts identify different launches")
    row, = release["launches"]
    require(release.get("clients_sha256") == clients and row["mode"] == "full"
            and all(type(row[k]) is int and row[k] == timing[k] for k in ("user_us", "sys_us"))
            and type(row["cpu_us"]) is int and row["cpu_us"] * 1000 == timing["cpu_ns"],
            "incorrect combined clients or child CPU")
    spatial_counts = [(s["offset"], s["loads"]) for s in spatial["sites"]]
    require(spatial_counts == [(s["offset"], s["loads"]) for s in temporal_report["sites"]],
            "spatial and temporal eligible load counts differ within a single execution")
    receipt = dict(schema=1, authority="diagnostic_only", launches=1, pid=obs["pid"], cpu_ns=timing["cpu_ns"],
                   clients_sha256=clients, elf_sha256=elf_sha, stdin_sha256=stdin_sha, stdout_sha256=stdout_sha,
                   spatial_path="full/full/sample.json", temporal_path="full/full/temporal.json",
                   spatial_sha256=obs["sample_sha256"], temporal_sha256=timing["sample_sha256"],
                   clock_path="full/full/clock.bin", clock_sha256=obs["clock_sha256"],
                   process_sha256=obs["process_sha256"], production_sha256=obs["production_sha256"],
                   full_result_sha256=obs["result_sha256"], temporal_result_sha256=elf_digest(files / "temporal-result.json"))
    return receipt, temporal_report


def inspect_temporal(root, elf_sha, stdin_sha, stdout_sha, probe_sha=temporal.PROBE_SHA):
    expected, temporal_report = _receipts(root, elf_sha, stdin_sha, stdout_sha, probe_sha)
    saved = read_json((root / "combined.json").read_bytes())
    require(saved == expected and type(saved["pid"]) is int and type(saved["cpu_ns"]) is int
            and type(saved["launches"]) is int and type(saved["schema"]) is int, "incorrect combined receipt")
    return temporal_report


def verify_tools(payload, probe):
    require(elf_digest(probe) == temporal.PROBE_SHA, "unregistered temporal sidecar")
    verified_runtime(payload)
    plan = read_json(PROTOCOL.read_bytes())
    for name in ("span.so", "cost_bench.py", "base/bench.py"):
        require(elf_digest(payload / name) == plan["tools_sha256"][name], "different registered component: " + name)


def capture(payload, probe, elf, stdin, out):
    payload, probe, elf, stdin, out = (Path(p).resolve() for p in (payload, probe, elf, stdin, out))
    elf_sha = static_elf(elf)
    verify_tools(payload, probe)
    plan = read_json(PROTOCOL.read_bytes())
    sys.path.insert(0, str(payload))
    try:
        runtime = runpy.run_path(str(payload / "cost_bench.py"))
    finally:
        sys.path.pop(0)
    out.mkdir(mode=0o700)
    full = out / "full"
    full.mkdir()
    files = full / "full"
    files.mkdir()
    input_copy = full / "stdin"
    input_copy.write_bytes(stdin.read_bytes())
    stdin_sha = elf_digest(input_copy)
    argv = runtime["command"]("full", elf, files)
    where = argv.index("-follow_children")
    argv[where:where] = ["-client", probe, "00000002", "-out " + runtime["quoted"](files / "temporal.json")]
    base = runtime["base"]
    old = signal.signal(signal.SIGALRM, base["timeout"])
    try:
        row = base["execute"](argv, files, input_copy, plan)
    finally:
        signal.signal(signal.SIGALRM, old)
    pid = row["launcher_pid"]
    runtime["finish_live"]("full", files, pid)
    require(elf_digest(elf) == elf_sha, "combined executable changed during execution")
    extras = dict(schema=1, authority="diagnostic_only", pid=pid, exit_code=row["exit_code"],
                  user_us=row["user_us"], sys_us=row["sys_us"], cpu_ns=row["cpu_us"] * 1000,
                  elf_sha256=elf_sha, stdin_sha256=stdin_sha, stdout_sha256=row["stdout_sha256"],
                  probe_sha256=temporal.PROBE_SHA, sample_sha256=elf_digest(files / "temporal.json"),
                  production_sha256=elf_digest(files / "production.json"))
    save_new(files / "temporal-result.json", extras)
    clients = {name: plan["tools_sha256"][name] for name in ("v11.so", "span.so")}
    clients["temporal.so"] = temporal.PROBE_SHA
    launch = dict(row, mode="full")
    save_new(full / "result.json", dict(elf_sha256=elf_sha, stdin_sha256=stdin_sha, authority="diagnostic_only",
                                        clients_sha256=clients, launches=[launch],
                                        final_sample="full/sample.json", stdout="full/stdout"))
    marker, _ = _receipts(out, elf_sha, stdin_sha, row["stdout_sha256"], temporal.PROBE_SHA)
    save_new(out / "combined.json", marker)
    return launch, extras
