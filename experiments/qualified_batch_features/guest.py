"""Collect native times and terminal v12 counters for the registered CPP20 cases."""
import argparse
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path
import platform
import sys
import urllib.error

ROOT = Path(__file__).resolve().parent
PILOT = ROOT / "pilot"
sys.path.insert(0, str(PILOT))
spec = importlib.util.spec_from_file_location("qualified_pilot", PILOT / "guest.py")
pilot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pilot)
v12 = pilot.source
require = v12.require


def scheduled(cases):
    for rep in range(1, 6):
        for offset in range(len(cases)):
            case = cases[(offset + 3 * (rep - 1)) % len(cases)]
            arms = ("native", "counters") if (offset + rep) % 2 else ("counters", "native")
            for kind in arms:
                if kind == "native" or rep <= 2:
                    yield case, kind, rep


def validate(case, kind, receipt, stdout, req, plan):
    correct = case["expected_status"] == "Accepted"
    require(stdout == case["solver_stdout"] and v12.sha(stdout.encode()) == case["solver_stdout_sha256"],
            "SSE stdout differs from the independent solver oracle")
    require(correct == (stdout == case["expected_stdout"]) and receipt.get("exit_code") == 0
            and receipt.get("cpu_time_ns", 0) > 0 and not receipt.get("stdout_truncated")
            and not receipt.get("cpu_time_sampling") and not receipt.get("cpu_time_normalization"), "incomplete trial")
    if kind == "native":
        pilot.validate(receipt, "native", req, stdout.encode(), case["expected_stdout"].encode(), None)
        require(receipt["status"] == case["expected_status"], "unexpected native verdict")
        return
    report, execution = receipt["work_measurement"], receipt["work_measurement_execution"]
    require(receipt["status"] == "Container Initialization Failed" and receipt.get("score") is None
            and execution["state"] == "complete" and execution["output_matches"] is correct
            and execution["termination_source"] == "stdout" and execution["nominal_limits"] == req["limits"]
            and execution["raw_limits"] == pilot.HARD and "clock" not in execution, "missing terminal diagnostic")
    require(report["engine_profile"] == v12.PROFILE and report["family"] == "cpp"
            and report["dispatch_profile"] == "default" and report["cpuid_profile"] == "x86-64-v2"
            and execution["authority"] == report["authority"] == "diagnostic_only" and report["complete"]
            and report["quality"] == "uninterrupted" and report["signals_observed"] == 0
            and report["processes"] == report["images"] == 1 and report["unsupported_rep_entries"] == 0,
            "unexpected measurement profile")
    rep = report["rep_breakdown"]
    require(rep["version"] == 1 and report["rep_bytes"] == rep["movs_bytes"] + rep["stos_bytes"]
            and report["instructions"] > 0 and report["latency_bound"]["version"] == 1
            and report["latency_bound"]["cycles_x16"] >= 4 * report["instructions"]
            and report["memory_operand_bytes"]["version"] == 1 and report["memory_operand_bytes"]["bytes"] > 0,
            "missing or incompatible terminal counters")
    installation = report["installation"]
    files = plan["installed_files"]
    require(installation["unchanged_before_after"] and installation["launcher_sha256"] == files["dynamorio/bin64/drrun"]
            and installation["client_sha256"] == files["lib/libdurante.so"]
            and installation["release_core_sha256"] == files["dynamorio/lib64/release/libdynamorio.so"],
            "receipt installation differs from the frozen runtime")


def collect(work_root, trace_dir):
    out = ROOT / "output"
    out.mkdir()
    case_manifest = ROOT / "case_manifest.json"
    cases = v12.read_json(case_manifest)["cases"]
    require(len(cases) == len({case["case_id"] for case in cases}) == 15, "expected 15 separate cases")
    plan = v12.read_json(PILOT / "plan.json")
    for file, key in ((pilot.SERVER, "server_sha256"), (PILOT / "runtime.squashfs", "runtime_sha256"),
                      (PILOT / "corpus/manifest.json", "manifest_sha256")):
        require(v12.sha(file.read_bytes()) == plan[key], f"different {key}")
    cg = Path("/sys/fs/cgroup") / Path("/proc/self/cgroup").read_text().strip().split("::")[-1].lstrip("/")
    host = dict(boot_sha256=v12.sha(Path("/proc/sys/kernel/random/boot_id").read_text().strip().encode()),
                kernel=platform.release(), cpu=1, affinity=sorted(os.sched_getaffinity(0)),
                cpuset_cpus_effective=(cg / "cpuset.cpus.effective").read_text().strip(),
                cpu1_thread_siblings=Path("/sys/devices/system/cpu/cpu1/topology/thread_siblings_list").read_text().strip(),
                steal_ticks_before=v12.cpu_steal(), case_manifest_sha256=v12.sha(case_manifest.read_bytes()),
                plan_sha256=v12.sha((PILOT / "plan.json").read_bytes()), runtime_sha256=plan["runtime_sha256"],
                server_sha256=plan["server_sha256"])
    require(host["affinity"] == [1] and host["cpuset_cpus_effective"] == host["cpu1_thread_siblings"] == "1",
            "unqualified CPU placement")
    require(plan["runtime_image"] == "sha256:" + plan["runtime_sha256"], "runtime image mismatch")
    measured = {**pilot.measurements(), "AONOHAKO_DURANTE_LATENCY_BOUND": "v1",
                "AONOHAKO_DURANTE_MEMORY_OPERAND_BYTES": "v1"}
    with ExitStack() as stack:
        native = stack.enter_context(pilot.worker("native", work_root, out, plan["runtime_image"], {}))
        counters = stack.enter_context(pilot.worker("raw", work_root, out, plan["runtime_image"], measured))
        services = {"native": native, "counters": counters}
        host["workers"] = {kind: pid for kind, (_, pid) in services.items()}
        for pid in host["workers"].values():
            require(v12.sha(Path(f"/proc/{pid}/exe").read_bytes()) == host["server_sha256"], "incorrect running worker")
        pilot.write_json(out / "platform.json", host)
        with (out / "trials.jsonl").open("xb") as journal, (out / "exec-trace.txt").open("x") as trace:
            for seq, (case, kind, rep) in enumerate(scheduled(cases)):
                binary = (PILOT / "corpus" / case["binary"]).read_bytes()
                stdin, expected = case["stdin"].encode(), case["expected_stdout"].encode()
                require(v12.sha(binary) == case["binary_sha256"] and v12.sha(stdin) == case["stdin_sha256"]
                        and v12.sha(expected) == case["stdout_sha256"], "trial input differs from its registration")
                req = pilot.request_for(case["problem"], kind, pilot.NOMINAL["time_ms"], stdin, expected, binary, None)
                (trace_dir / "trace").write_text("\n")
                try:
                    receipt, stdout = pilot.post(services[kind][0], "/execute", req,
                                                 timeout=pilot.HARD["wall_time_ms"] / 1000 + 30)
                except urllib.error.HTTPError as error:
                    pilot.write_json(out / "failed-http.json", {"sequence": seq, "status": error.code,
                                                               "reason": error.read(16 << 10).decode(errors="replace")})
                    raise
                row = {k: case[k] for k in ("case_id", "problem", "role", "split", "binary_sha256", "stdin_sha256")}
                row.update(sequence=seq, kind=kind, rep=rep, expected_stdout_sha256=case["stdout_sha256"],
                           stdout=stdout, stdout_sha256=v12.sha(stdout.encode()),
                           request_sha256=v12.sha(json.dumps(req).encode()), receipt=receipt)
                try:
                    row["execs"] = v12.collect_execs(trace_dir, trace, seq, kind)
                except (ValueError, OSError):
                    pilot.write_json(out / "failed-trial.json", row)
                    raise
                journal.write(pilot.packed(row) + b"\n")
                journal.flush()
                validate(case, kind, receipt, stdout, req, plan)
    host["steal_ticks_after"] = v12.cpu_steal()
    pilot.write_json(out / "platform.json", host)


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("phase", choices=("calibrate",), help="pinned pilot bootstrap argument")
    cli.add_argument("--work-root", type=Path, required=True)
    cli.add_argument("--trace-dir", type=Path, required=True)
    opts = cli.parse_args()
    collect(opts.work_root, opts.trace_dir)
