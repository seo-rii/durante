"""Scale frozen v11 work by one native calibration of the same trusted static Linux x86-64 ELF."""
import argparse
from dataclasses import dataclass
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import platform
import signal
import struct
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from cpp20_locality.score import hot_site
from cpp20_locality.verify import release_reader, require, sha, verify_counters
from first_run_sampler.profile import digest, elf_digest, read_json
from guarded_native.evidence import REFERENCE, REFERENCE_SHA
from guarded_native.transfer import scaled_ns, work
from guarded_sampler.guard import decision

PROTOCOL = HERE.parent / "guarded_sampler/protocol.json"


def static_elf(path):
    with path.open("rb") as f:
        header = f.read(64)
        require(len(header) == 64 and header[:7] == b"\x7fELF\x02\x01\x01", "expected a little-endian ELF64")
        kind, machine = struct.unpack_from("<HH", header, 16)
        phoff, = struct.unpack_from("<Q", header, 32)
        width, count = struct.unpack_from("<HH", header, 54)
        require(kind in (2, 3) and machine == 62 and width == 56 and 0 < count < 65535, "expected x86-64 executable")
        f.seek(phoff)
        segments = f.read(width * count)
        require(len(segments) == width * count, "truncated ELF program headers")
        types = [s[0] for s in struct.iter_unpack("<IIQQQQQQ", segments)]
        require(1 in types and 3 not in types, "expected a static executable without a program interpreter")
    return elf_digest(path)


def weights():
    raw = REFERENCE.read_bytes()
    require(sha(raw) == REFERENCE_SHA, "changed #154 reference")
    return read_json(raw)["combined_reference_weights"]


def measured(run, elf_sha):
    result_raw = (run / "result.json").read_bytes()
    result = read_json(result_raw)
    input_sha = elf_digest(run / "stdin")
    require(result["authority"] == "diagnostic_only" and result["elf_sha256"] == elf_sha
            and result["stdin_sha256"] == input_sha, "wrong #82 executable, input or authority")
    launches = result["launches"]
    modes = [r["mode"] for r in launches]
    require(modes in (["full"], ["guarded"], ["guarded", "full"]), "unrecognized #82 execution path")
    require(("decision" in result) == ("guarded" in modes), "unexpected or missing guard decision")
    require(result["final_sample"] == modes[-1] + "/sample.json" and result["stdout"] == modes[-1] + "/stdout",
            "final report does not belong to the selected launch")
    require(len({r["launcher_pid"] for r in launches}) == len(launches), "duplicate execution PID")
    plan = read_json(PROTOCOL.read_bytes())
    reader = release_reader(plan["tools_sha256"]["production_reader.py"])
    output_sha = launches[0]["stdout_sha256"]
    for row in launches:
        mode, pid = row["mode"], row["launcher_pid"]
        directory = run / mode
        require(type(pid) is int and pid > 0 and row["exit_code"] == 0 and (directory / "stderr").stat().st_size == 0
                and elf_digest(directory / "stdout") == row["stdout_sha256"] == output_sha, "inconsistent launch or output")
        record = directory / "records" / f"process-{pid}.jsonl"
        require(set(record.parent.iterdir()) == {record}, "unexpected process record set")
        process_raw = record.read_bytes()
        production_raw = (directory / "production.json").read_bytes()
        prod = read_json(production_raw)
        clock = (directory / "clock.bin").read_bytes()
        verify_counters(process_raw, prod, clock, "live", pid, plan["work_budget"], reader)
        sample_raw = (directory / "sample.json").read_bytes()
        sample = read_json(sample_raw)
        hot_site(sample, "live_span", pid, {})
        if mode == "guarded":
            observed = decision(sample, read_json((directory / "guard.json").read_bytes()), pid)
            require(result.get("decision") == observed and (observed["status"] == "run_full") == (len(launches) == 2),
                    "final source disagrees with guarded coverage")
    return {"elf_sha256": elf_sha, "stdin_sha256": input_sha, "stdout_sha256": output_sha,
            "result_sha256": sha(result_raw), "mode": modes[-1], "launch_count": len(launches), "pid": pid,
            "process_sha256": sha(process_raw), "production_sha256": sha(production_raw),
            "clock_sha256": sha(clock), "sample_sha256": sha(sample_raw),
            "instructions": prod["instructions"], "rep_bytes": prod["rep_bytes"],
            "movs_bytes": prod["rep_breakdown"]["movs_bytes"], "stos_bytes": prod["rep_breakdown"]["stos_bytes"]}


def native_once(elf, stdin):
    # Only this direct child contributes to the native calibration. The parent's validation and polling are excluded.
    affinity = sorted(os.sched_getaffinity(0))
    with tempfile.TemporaryFile() as source, tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        source.write(stdin)
        source.seek(0)
        with subprocess.Popen([elf], stdin=source, stdout=stdout, stderr=stderr, start_new_session=True) as child:
            deadline = time.monotonic() + 120
            try:
                while True:
                    pid, status, usage = os.wait4(child.pid, os.WNOHANG)
                    if pid:
                        child.returncode = os.waitstatus_to_exitcode(status)
                        break
                    if time.monotonic() >= deadline:
                        raise TimeoutError("native calibration exceeded 120 seconds")
                    time.sleep(0.005)
            finally:
                if child.returncode is None:
                    try:
                        os.killpg(child.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    child.wait()
        require(child.returncode == 0 and os.fstat(stderr.fileno()).st_size == 0, "native calibration failed or wrote to stderr")
        require(affinity == sorted(os.sched_getaffinity(0)), "launcher affinity changed during calibration")
        stdout.seek(0)
        user = round(usage.ru_utime * 1_000_000)
        system = round(usage.ru_stime * 1_000_000)
        require(user + system > 0, "zero native CPU time")
        return {"pid": pid, "user_us": user, "sys_us": system, "cpu_ns": 1000 * (user + system),
                "stdout_sha256": hashlib.file_digest(stdout, "sha256").hexdigest(), "affinity": affinity,
                "single_cpu": len(affinity) == 1,
                "machine": platform.machine(), "kernel": platform.release()}


def calibration_digest(anchor, native):
    return sha(json.dumps([anchor, native], sort_keys=True, separators=(",", ":")).encode())


def save_new(path, obj):
    with tempfile.NamedTemporaryFile(dir=path.parent) as temp:
        temp.write((json.dumps(obj, indent=2) + "\n").encode())
        temp.flush()
        os.link(temp.name, path)


def calibrate(elf, stdin, run, out):
    require(not out.exists(), "calibration policy already exists")
    digest_elf = static_elf(elf)
    anchor = measured(run, digest_elf)
    raw = stdin.read_bytes()
    require(anchor["mode"] == "full" and anchor["launch_count"] == 1 and anchor["stdin_sha256"] == sha(raw),
            "expected the initial full measurement of this calibration input")
    native = native_once(elf, raw)
    require(native["stdout_sha256"] == anchor["stdout_sha256"] and elf_digest(elf) == digest_elf,
            "calibration output or executable changed")
    policy = {"schema": 1, "authority": "diagnostic_only", "reference_sha256": REFERENCE_SHA,
              "combined_reference_weights": weights(), "anchor": anchor, "native": native,
              "calibration_sha256": calibration_digest(anchor, native)}
    save_new(out, policy)
    return policy


@dataclass(frozen=True)
class CalibratedClock:
    policy_sha256: str
    elf_sha256: str
    native_ns: int
    weights_ns: dict
    calibration_work: Fraction

    def predict_ns(self, progress):
        return scaled_ns(self.native_ns, work(progress, self.weights_ns), self.calibration_work)


def load_clock(policy_file, elf):
    raw = policy_file.read_bytes()
    policy = read_json(raw)
    w = weights()
    require(type(policy["schema"]) is int and policy["schema"] == 1 and policy["authority"] == "diagnostic_only"
            and policy["reference_sha256"] == REFERENCE_SHA and policy["combined_reference_weights"] == w,
            "unsupported anchor policy or clock coefficients")
    anchor, native = policy["anchor"], policy["native"]
    digest_elf = static_elf(elf)
    require(policy["calibration_sha256"] == calibration_digest(anchor, native), "changed calibration record")
    require(all(type(anchor[k]) is int and 0 <= anchor[k] < 2**64
                for k in ("instructions", "movs_bytes", "stos_bytes"))
            and anchor["rep_bytes"] == anchor["movs_bytes"] + anchor["stos_bytes"]
            and sha(struct.pack("<6Q", 1, 0, anchor["instructions"], anchor["movs_bytes"], anchor["stos_bytes"], 0))
            == anchor["clock_sha256"], "anchor counts differ from the terminal clock")
    affinity = native.get("affinity")
    require(type(native.get("pid")) is int and native["pid"] > 0 and isinstance(affinity, list) and affinity
            and all(type(cpu) is int and cpu >= 0 for cpu in affinity) and sorted(set(affinity)) == affinity
            and native.get("single_cpu") is (len(affinity) == 1) and native.get("machine") in ("x86_64", "amd64")
            and isinstance(native.get("kernel"), str) and native["kernel"], "invalid native calibration provenance")
    require(anchor["elf_sha256"] == digest_elf and anchor["mode"] == "full" and anchor["launch_count"] == 1
            and all(digest(anchor[k + "_sha256"]) for k in ("stdin", "stdout", "result", "process", "production", "clock", "sample"))
            and native["stdout_sha256"] == anchor["stdout_sha256"] and type(anchor["pid"]) is int and anchor["pid"] > 0
            and all(type(anchor[k]) is int and anchor[k] >= 0 for k in w) and anchor["instructions"] > 0
            and all(type(native[k]) is int and native[k] >= 0 for k in ("user_us", "sys_us", "cpu_ns"))
            and native["cpu_ns"] == 1000 * (native["user_us"] + native["sys_us"]) > 0,
            "anchor contradicts its ELF, native accounting or provenance")
    weights_ns = {k: Fraction(v) for k, v in w.items()}
    return CalibratedClock(sha(raw), digest_elf, native["cpu_ns"], weights_ns, work(anchor, weights_ns))


def predict(policy_file, elf, run, limit_ms=None):
    clock = load_clock(policy_file, elf)
    current = measured(run, clock.elf_sha256)
    predicted = clock.predict_ns(current)
    result = {"authority": "diagnostic_only", "policy_sha256": clock.policy_sha256, "observation": current,
              "anchor_native_ns": clock.native_ns, "prediction_ns": predicted}
    if limit_ms is not None:
        require(type(limit_ms) is int and limit_ms > 0, "expected a positive whole-millisecond deadline")
        result.update(limit_ms=limit_ms, implied_tle=predicted > limit_ms * 1_000_000)
    return result


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    commands = cli.add_subparsers(dest="action", required=True)
    first = commands.add_parser("calibrate", help="capture one direct native wait4 CPU measurement")
    later = commands.add_parser("predict", help="scale a new metered run without a native rerun")
    for command in (first, later):
        command.add_argument("--elf", required=True, type=Path, help="immutable trusted static ELF")
        command.add_argument("--run", required=True, type=Path, help="standalone guarded_sampler/run.py output")
    first.add_argument("--stdin", type=Path, required=True)
    first.add_argument("--out", type=Path, required=True, help="new private calibration policy file")
    later.add_argument("--policy", type=Path, required=True, help="trusted policy from calibrate")
    later.add_argument("--limit-ms", type=int)
    args = cli.parse_args()
    if args.action == "calibrate":
        value = calibrate(args.elf.resolve(), args.stdin, args.run, args.out)
    else:
        value = predict(args.policy, args.elf.resolve(), args.run, args.limit_ms)
    print(json.dumps(value))
