"""Collect native, diagnostic and live-clock trials from the frozen input bundle."""
import hashlib
import json
import os
from pathlib import Path
import platform
import signal
import struct
import subprocess
import time

from production_reader import collect

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "output"
CLK = struct.Struct("<6Q")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def quoted(path):
    text = str(path)
    require('"' not in text, "unsupported path")
    return '"' + text + '"'


def command(arm, program, directory):
    if arm in ("warmup", "native"):
        return [program]
    (directory / "records").mkdir()
    options = "-outdir " + quoted(directory / "records") + " -rep_bytes -cpuid_profile x86-64-v2"
    if arm in ("live", "live_span"):
        with (directory / "clock.bin").open("xb") as stream:
            stream.write(bytes(CLK.size))
        options += " -live_work_v1 " + quoted(directory / "clock.bin")
    else:
        options += " -latency_bound -memory_operand_bytes"
    args = [ROOT / "dynamorio/bin64/drrun", "-quiet", "-client", ROOT / "v11.so", "00000000", options]
    if arm in ("diag_near", "diag_span", "live_span"):
        client = "near.so" if arm == "diag_near" else "span.so"
        args.extend(["-client", ROOT / client, "00000001", "-out " + quoted(directory / "sample.json")])
    return args + ["-follow_children", "-disable_traces", "-vm_size", "32M", "-vmheap_size", "32M", "--", program]


def clock_snapshot(stream, previous, budget):
    words = CLK.unpack(os.pread(stream.fileno(), CLK.size, 0))
    ready, invalid, ins, movs, stos, overflow = words
    require(ready in (0, 1) and invalid == 0 and overflow == 0, "invalid live-work snapshot")
    require(all(a <= b for a, b in zip(previous, words)), "decreasing live-work snapshot")
    require(ready or (ins, movs, stos) == (0, 0, 0), "unready live-work counters")
    require(ins + movs + stos <= budget, "live work budget exceeded")
    return words


def execute(cmd, directory, stdin_path, plan):
    live_path = directory / "clock.bin"
    clock = live_path.open("rb") if live_path.exists() else None
    polls = 0
    snapshot = (0,) * 6
    with stdin_path.open("rb") as stdin, (directory / "stdout").open("xb") as stdout, (directory / "stderr").open("xb") as stderr:
        start = time.monotonic_ns()
        proc = subprocess.Popen(cmd, stdin=stdin, stdout=stdout, stderr=stderr, start_new_session=True)
        signal.alarm(plan["trial_timeout_s"])
        try:
            if clock:
                while True:
                    pid, status, usage = os.wait4(proc.pid, os.WNOHANG)
                    if pid:
                        break
                    snapshot = clock_snapshot(clock, snapshot, plan["work_budget"])
                    polls += 1
                    time.sleep(plan["poll_interval_ms"] / 1000)
            else:
                _, status, usage = os.wait4(proc.pid, 0)
            wall = time.monotonic_ns() - start
            proc.returncode = os.waitstatus_to_exitcode(status)
            require(proc.returncode == 0, f"{directory} failed with code {proc.returncode}")
            if clock:
                snapshot = clock_snapshot(clock, snapshot, plan["work_budget"])
                require(polls > 0 and snapshot[0] == 1, "missing live clock")
        except BaseException:
            if proc.returncode is None:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait()
            raise
        finally:
            signal.alarm(0)
            if clock:
                clock.close()
    require((directory / "stderr").stat().st_size == 0, f"{directory}: nonempty stderr")
    user_us = round(usage.ru_utime * 1_000_000)
    sys_us = round(usage.ru_stime * 1_000_000)
    row = dict(launcher_pid=proc.pid, exit_code=proc.returncode, user_us=user_us, sys_us=sys_us,
               cpu_us=user_us + sys_us, wall_us=round(wall / 1000), stdout_sha256=digest(directory / "stdout"))
    if clock:
        row["clock_polls"] = polls
    return row


def one(item, case, sequence, plan):
    arm = item["arm"]
    directory = OUT / "runs" / f"{sequence:03d}"
    directory.mkdir()
    argv = command(arm, ROOT / "bin" / case["program"], directory)
    row = execute(argv, directory, OUT / "inputs" / case["stdin_sha256"], plan)
    pid = row["launcher_pid"]
    require(row["stdout_sha256"] == case["stdout_sha256"], f"{case['case_id']}: bad stdout")
    require(row["cpu_us"] > 0, "missing wait4 rusage")
    if arm not in ("native", "warmup"):
        m = collect(directory / "records", pid)
        require(m["valid"] and m["complete"] and m["quality"] == "uninterrupted" and m["signals_observed"] == 0
                and m["process_count"] == m["image_count"] == m["segments"][0]["threads"] == 1
                and m["root_pid"] == pid and m["processes"][0]["exit_code"] == 0, "v11 process coverage")
        require(m["authority"] == "diagnostic_only" and m["integrity_verified"] is False and
                m["measurement_contract"]["trusted_verdict_eligible"] is False, "v11 authority")
        require(m["cpuid_profile"] == "x86-64-v2" and m["rep_telemetry_status"] == "movs_stos_only"
                and m["rep_bytes_enabled"] and m["unsupported_rep_entries"] == 0 and m["rep_breakdown"]["version"] == 1
                and m["rep_bytes"] == m["rep_breakdown"]["movs_bytes"] + m["rep_breakdown"]["stos_bytes"], "v11 counters")
        diag = arm.startswith("diag")
        require(("latency_bound" in m) == ("memory_operand_bytes" in m) == diag, "wrong mode")
        if diag:
            require(m["latency_bound"]["version"] == m["memory_operand_bytes"]["version"] == 1
                    and m["latency_bound"]["cycles_x16"] >= 4 * m["instructions"] > 0
                    and m["memory_operand_bytes"]["bytes"] > 0, "invalid diagnostic counters")
        else:
            words = CLK.unpack((directory / "clock.bin").read_bytes())
            require(words == (1, 0, m["instructions"], m["rep_breakdown"]["movs_bytes"],
                              m["rep_breakdown"]["stos_bytes"], 0), "terminal work clock mismatch")
        save(directory / "production.json", m)
        if arm in ("diag_near", "diag_span", "live_span"):
            s = json.loads((directory / "sample.json").read_bytes())
            require(s["schema"] == (1 if arm == "diag_near" else 2) and s["pid"] == pid and s["period"] == 1024
                    and s["line_bytes"] == 64 and s["threads"] == 1, "sampler config")
    receipts = {p.relative_to(OUT).as_posix(): digest(p) for p in sorted(directory.rglob("*")) if p.is_file()}
    return dict(sequence=sequence, **item, **row, receipts=receipts)


def steal(cpu):
    prefix = f"cpu{cpu} "
    return int(next(line for line in Path("/proc/stat").read_text().splitlines() if line.startswith(prefix)).split()[8])


def timeout(_signal_number, _frame):
    raise TimeoutError("per-trial deadline reached")


def main():
    plan = json.loads((ROOT / "protocol.json").read_bytes())
    for name, bound in plan["tools_sha256"].items():
        require(digest(ROOT / name) == bound, f"changed payload {name}")
    cpu = plan["cpu"]
    require(os.sched_getaffinity(0) == {cpu}, "unexpected affinity")
    group = Path("/sys/fs/cgroup") / Path("/proc/self/cgroup").read_text().strip().split(":")[-1].lstrip("/")
    effective = (group / "cpuset.cpus.effective").read_text().strip()
    require(effective == str(cpu), "unexpected effective cgroup")
    OUT.mkdir()
    (OUT / "runs").mkdir()
    (OUT / "inputs").mkdir()
    cases = {c["case_id"]: c for c in json.loads((ROOT / "cases.json").read_bytes())}
    for c in cases.values():
        path = OUT / "inputs" / c["stdin_sha256"]
        path.write_bytes(c["stdin"].encode())
        require(digest(path) == c["stdin_sha256"] and hashlib.sha256(c["stdout"].encode()).hexdigest() == c["stdout_sha256"]
                and digest(ROOT / "bin" / c["program"]) == c["binary_sha256"], "fixture mismatch")
    before = steal(cpu)
    rows = []
    with (OUT / "progress.jsonl").open("x") as progress:
        for sequence, item in enumerate(plan["schedule"]):
            row = one(item, cases[item["case_id"]], sequence, plan)
            rows.append(row)
            progress.write(json.dumps(row) + "\n")
            progress.flush()
    config = dict(cpu=cpu, affinity=sorted(os.sched_getaffinity(0)), cgroup_allowed_cpus=effective,
                  kernel=platform.release(), cpu_model=next(line.split(":", 1)[1].strip() for line in
                  Path("/proc/cpuinfo").read_text().splitlines() if line.startswith("model name")),
                  clock_tick_hz=os.sysconf("SC_CLK_TCK"), cpu_steal_ticks=steal(cpu) - before)
    save(OUT / "dataset.json", dict(schema=1, protocol_sha256=digest(ROOT / "protocol.json"), platform=config, rows=rows))


if __name__ == "__main__":
    signal.signal(signal.SIGALRM, timeout)
    main()
