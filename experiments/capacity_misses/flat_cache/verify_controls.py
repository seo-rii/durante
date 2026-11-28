"""Exercise the full ML4 probe on the registered client and the flat-checkpoint candidate."""
import argparse
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "experiments/l1d_probe"))
from check_control import EXPECTED_CACHE, EXPECTED_WORK

collect_work = runpy.run_path(str(ROOT / "collect.py"))["collect"]
INCOMPLETE = b"durante: incomplete cache probe measurement"


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def trial(drrun, client, program, arguments, data, oracle, cache=True):
    with tempfile.TemporaryDirectory() as temporary:
        slot = Path(temporary)
        reports = slot / "reports"
        reports.mkdir()
        command = [drrun, "-quiet", "-disable_traces", "-c", client, "-outdir", reports, "-rep_bytes",
                   "-latency_bound", "-cpuid_profile", "x86-64-v2"]
        if cache:
            command += ["-probe_l1d", "-probe_outer"]
        with subprocess.Popen([*command, "--", program, *arguments], cwd=slot, stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              env={"PATH": os.defpath, "LC_ALL": "C", "DYNAMORIO_CONFIGDIR": str(slot)}) as proc:
            try:
                stdout, stderr = proc.communicate(data, timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
                raise
        require(proc.returncode == 0 and stdout == oracle, f"{program.name} exit/output mismatch: {stderr!r}")
        work = collect_work(reports, proc.pid)
        require(work["valid"] and work["complete"] and work["cpuid_profile"] == "x86-64-v2"
                and work["image_count"] == work["process_count"] == work["segments"][0]["threads"] == 1
                and work["processes"][0]["exit_code"] == 0 and work["latency_bound"]["cycles_x16"] > 0,
                f"invalid work receipt: {work}")
        end = json.loads((reports / f"process-{proc.pid}.jsonl").read_bytes().splitlines()[-1])
        return {k: v for k, v in end.items() if k not in ("pid", "segment")}, stderr.splitlines()


def verify(args):
    result = {}
    with tempfile.TemporaryDirectory() as temporary:
        signal_program = Path(temporary) / "signal-checkpoints"
        subprocess.run([args.cc, "-static", "-nostdlib", "-no-pie", "-Wl,--build-id=none",
                        ROOT / "tests/live_work_eligibility.S", "-o", signal_program], check=True, timeout=20)
        cases = [
            ("memory", args.fixtures / "l1d_probe_control", [], b"", b""),
            ("memory-warm", args.fixtures / "l1d_probe_control", ["warm"], b"", b""),
            ("address-dependency", args.fixtures / "l1d_dependency_control", [], b"", b""),
            ("handled-signal", signal_program, ["signal"], b"\x02\x03", b"\x01\x02"),
        ]
        for name, program, arguments, data, oracle in cases:
            native = subprocess.run([program, *arguments], capture_output=True, input=data, check=True, timeout=20)
            require(native.stdout == oracle and not native.stderr, f"{name} native oracle")
            reference = None
            for client in (args.original, args.fast):
                work, lines = trial(args.drrun, client, program, arguments, data, oracle)
                if name == "handled-signal":
                    require(lines == [INCOMPLETE] and work["quality"] == "signals_observed"
                            and work["signals_observed"] == 1, f"{name} partial diagnostic escaped: {lines}")
                    plain, noise = trial(args.drrun, client, program, arguments, data, oracle, cache=False)
                    require(not noise and plain == work, "handled signal changed ordinary work")
                else:
                    require(work["quality"] == "uninterrupted" and work["signals_observed"] == 0,
                            f"{name} interrupted")
                    require(len(lines) == 2 and lines[1].startswith(b"PROBE_OUTER capacities=4096,32768,524288 "),
                            f"{name} missing full cache report: {lines}")
                    if name == "address-dependency":
                        require(lines[0] == b"PROBE_L1D refs=4 lines=4 misses=4 skipped=0 rep=0 threads=1"
                                and b" dependent=2,2,2 l1d_dependent=2 " in lines[1], "dependent miss oracle")
                    else:
                        expected = EXPECTED_WORK.copy()
                        if arguments:
                            expected["instructions"] += 262145
                        require(lines[0] == EXPECTED_CACHE and all(work[k] == v for k, v in expected.items()),
                                "read/write/RMW/cross-line/REP oracle")
                if reference is None:
                    reference = work, lines
                else:
                    require(reference == (work, lines), f"{name} changed sealed work or cache counters")
            result[name] = dict(work=work, diagnostic=[line.decode() for line in lines])
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("drrun", "original", "fast", "fixtures"):
        parser.add_argument("--" + option, type=lambda s: Path(s).resolve(strict=True), required=True)
    parser.add_argument("--cc", default="cc")
    print(json.dumps(verify(parser.parse_args()), indent=2))
