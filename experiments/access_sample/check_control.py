#!/usr/bin/env python3
"""Check hand-counted site totals, pair phase, relocation, and runtime refusals."""
import argparse
import json
import runpy
from pathlib import Path
import subprocess
import tempfile

from study import read_sample, validate_sample

HERE = Path(__file__).resolve().parent
quoted = runpy.run_path(str(HERE.parent / "rust_instruction_mix/collect.py"))["quoted"]
COUNTS = ("loads", "pairs", "near_pairs", "far_pairs")


def build(folder, source, *flags, pie=False):
    obj = folder / "control.o"
    binary = folder / "control"
    subprocess.run(["cc", "-c", *flags, source, "-o", obj], check=True)
    link = ["-pie", "--no-dynamic-linker"] if pie else ["-static"]
    subprocess.run(["ld", "--build-id=none", *link, "-o", binary, obj], check=True)
    return binary


def offsets(binary):
    headers = subprocess.check_output(["readelf", "-lW", binary], text=True)
    base = min(int(line.split()[2], 16) for line in headers.splitlines() if line.lstrip().startswith("LOAD "))
    syms = subprocess.check_output(["nm", "--defined-only", binary], text=True)
    return {name: int(addr, 16) - base for line in syms.splitlines()
            for addr, _, name in [line.split()] if name.endswith("_site")}


def marked(args, trial, binary, *, occupied=False):
    trial.mkdir()
    (trial / "records").mkdir()
    sample = trial / "sample.json"
    if occupied:
        sample.write_bytes(b"existing report")
    command = [args.drrun, "-quiet", "-client", args.meter, "00000000",
               f"-outdir {quoted(trial / 'records')} -rep_bytes -cpuid_profile x86-64-v2 -latency_bound -memory_operand_bytes",
               "-client", args.probe, "00000001", f"-out {quoted(sample)}", "-follow_children", "-disable_traces",
               "-vm_size", "32M", "-vmheap_size", "32M", "--", binary]
    with subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as proc:
        stdout, stderr = proc.communicate(timeout=30)
        return proc.pid, proc.returncode, stdout, stderr, sample


def native(binary):
    return subprocess.run([binary], stdin=subprocess.DEVNULL, capture_output=True, timeout=10, check=True).stdout


def check_sites(args, trial, binary, names):
    pid, status, output, stderr, path = marked(args, trial, binary)
    if status != 0 or stderr:
        raise ValueError(f"marked exit {status}: {stderr!r}")
    data = read_sample(path.read_bytes())
    validate_sample(data, pid)
    symbols = offsets(binary)
    expected = [{"offset": symbols[name], **dict(zip(COUNTS, totals, strict=True))} for name, totals in names.items()]
    if data["sites"] != sorted(expected, key=lambda row: row["offset"]):
        raise ValueError(f"{trial.name}: got {data['sites']}, expected {expected}")
    return output


def run(args, tmp):
    lengths = (1, 2, 1023, 1024, 1025, 1026, 2049, 2050)
    for visits in lengths:
        binary = build(tmp, HERE / "control.S", f"-DVISITS={visits}")
        if native(binary):
            raise ValueError("unexpected native output")
        pairs = (visits // 1024) + int(visits % 1024 >= 2)
        expected = {"near_site": (visits, pairs, pairs, 0), "far_site": (visits, pairs, 0, pairs),
                    "once_site": (1, 0, 0, 0)}
        if check_sites(args, tmp / f"visits-{visits}", binary, expected):
            raise ValueError("unexpected marked output")

    address = build(tmp, HERE / "address_control.S")
    expected = {name: (2050, 3, 0, 3) for name in ("stack_site", "scaled_site")}
    if native(address) or check_sites(args, tmp / "address", address, expected):
        raise ValueError("unexpected address control output")

    phase = build(tmp, HERE / "phase_control.S", pie=True)
    reported_pcs = [native(phase)]
    for repeat in range(3):
        reported_pcs.append(check_sites(args, tmp / f"phase-{repeat}", phase, {"phase_site": (2050, 3, 3, 0)}))
    if any(len(raw) != 8 or int.from_bytes(raw, "little") == 0 for raw in reported_pcs):
        raise ValueError("invalid application PC output")
    # Independently enumerate the true trace, including the final odd visit.
    lines = [2 if i % 2 and i % 1024 != 1 else 0 for i in range(2050)]
    far = sum(abs(a-b) > 1 for a, b in zip(lines, lines[1:]))
    if far != 2044:
        raise ValueError("phase control trace changed")

    pid, status, _, stderr, path = marked(args, tmp/"occupied", phase, occupied=True)
    if status == 0 or b"cannot create output" not in stderr or path.read_bytes() != b"existing report":
        raise ValueError(f"{pid}: accepted an occupied sidecar, exit {status} {stderr!r}")

    # RIP-relative scalar load counts towards the production byte counter and
    # deliberately falls outside the sampler opcode/addressing subset.
    zero = tmp / "zero.S"
    zero.write_text(".global _start\n.text\n_start: mov zero(%rip), %edi\nmov $60,%eax\nsyscall\n.data\nzero: .long 0\n")
    binary = build(tmp, zero)
    if native(binary) or check_sites(args, tmp/"zero", binary, {}):
        raise ValueError("unexpected zero-site control output")

    threaded = tmp / "threaded.c"
    threaded.write_text("#include <pthread.h>\nstatic void *work(void *p) { return p; }\n"
                        "int main(void) { pthread_t t; return pthread_create(&t, 0, work, 0) || pthread_join(t, 0); }\n")
    subprocess.run(["cc", "-O2", "-pthread", threaded, "-o", binary], check=True)
    native(binary)
    _, status, _, stderr, path = marked(args, tmp/"threaded", binary)
    if status == 0 or b"multiple application threads" not in stderr or (path.exists() and path.stat().st_size):
        raise ValueError(f"second thread did not invalidate the run, exit {status} {stderr!r}")

    return {"hand_test_visits": lengths, "phase_control_loads": 2050, "sampled_far_pairs": 0, "sampled_pairs": 3,
            "true_far_transitions": far, "true_transitions": len(lines)-1,
            "distinct_native_and_marked_pcs": len(set(reported_pcs)), "distinct_marked_pcs": len(set(reported_pcs[1:])),
            "rsp_scale_displacement": True, "zero_sites": True, "occupied_path_rejected": True, "second_thread_rejected": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("drrun", "meter", "probe"):
        parser.add_argument("--" + name, type=Path, required=True)
    with tempfile.TemporaryDirectory() as tmp:
        print(json.dumps(run(parser.parse_args(), Path(tmp)), indent=2))
