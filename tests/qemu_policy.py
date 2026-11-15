#!/usr/bin/env python3
"""Qualify guest counts together with aonohako's pinned CPU-policy backend.

The runtime directory must contain qemu-x86_64 and cpu-policy.so built from
aonohako 58fe9ce4ab60b0fc552da5ed799b2b3e9f502f72. No native DynamoRIO wrapper
is involved. All executed programs are small fixtures authored below.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import subprocess


def fixtures():
    cases = []

    def add(name, body="", *, count=None, policy="R", feature="avx", suffix="", code=0,
            timeout=False):
        end = f"mov $60,%eax\nmov ${code},%edi\nsyscall\n"
        source = (".global _start\n.text\n_start:\n" + body + end + suffix
                  + '\n.section .note.GNU-stack,"",@progbits\n')
        cases.append(dict(name=name, source=source, count=count, policy=policy,
                          feature=feature, code=code, timeout=timeout))

    add("scalar", count=3)
    add("scalar_exit126", count=3, code=126)
    add("scalar_exit255", count=3, code=255)
    add("loop", "mov $7,%ecx\nloop: dec %ecx\njnz loop\n", count=18)
    add("scalar_sse", "pxor %xmm0,%xmm0\naddsd %xmm0,%xmm0\n", count=5, feature="simd")
    add("packed_allowed_under_avx", "addps %xmm0,%xmm0\n", count=4)
    add("packed_blocked", "addps %xmm0,%xmm0\n", feature="simd", policy="RV")
    add("avx_raw", ".byte 0xc5,0xf8,0x77\n", policy="RV")
    add("avx_under_simd", ".byte 0xc5,0xf8,0x77\n", feature="simd", policy="RV")
    add("unreached_avx", "jmp skip\n.byte 0xc5,0xf8,0x77\nskip:\n", count=4)
    add("unreached_simd", "jmp skip\naddps %xmm0,%xmm0\nskip:\n", count=4, feature="simd")
    # do_gen_rep tests the low 16 bits after decrementing cx_next and handles
    # the last transfer outside the loop. The first reentry is at N=65,538,
    # then every 65,536 transfers; this is not native BB-entry accounting.
    for repeats, callbacks in ((0, 1), (1, 1), (65535, 1), (65536, 1),
                               (65537, 1), (65538, 2), (131073, 2), (131074, 3)):
        add(f"rep_stos_{repeats}", f"lea buffer(%rip),%rdi\nmov ${repeats},%ecx\n"
            "xor %eax,%eax\nrep stosb\n", count=6 + callbacks,
            suffix=".bss\nbuffer: .skip 131074\n")
    jit = ("mov $9,%eax\nxor %edi,%edi\nmov $4096,%esi\nmov $7,%edx\n"
           "mov $0x22,%r10d\nmov $-1,%r8\nxor %r9d,%r9d\nsyscall\n")
    add("jit_scalar", jit + "movl $0x9090c390,(%rax)\ncall *%rax\n", count=15)
    add("jit_avx", jit + "movl $0xc377f8c5,(%rax)\ncall *%rax\n", policy="RV")
    add("rewrite_avx", "call victim\nlea victim(%rip),%r12\nmov %r12,%rdi\n"
        "and $-4096,%rdi\nmov $4096,%esi\nmov $7,%edx\nmov $10,%eax\nsyscall\n"
        "movl $0xc377f8c5,(%r12)\ncall *%r12\n", policy="RV",
        suffix=".text\nvictim: ret\nnop\nnop\nnop\n")
    add("interior_avx", "lea immediate+2(%rip),%rax\njmp *%rax\n", policy="RV",
        suffix=".text\nimmediate: .byte 0x48,0xb8,0xc5,0xf8,0x77,0xc3,0,0,0,0\n")
    for name, number in (("fork", 57), ("exec", 59), ("clone", 56), ("clone3", 435)):
        add(name, f"mov ${number},%eax\nxor %edi,%edi\nsyscall\n", policy="RS")
    add("compat_fork", "mov $2,%eax\nint $0x80\n", policy="RV")
    add("x32", "mov $0x40000027,%eax\nsyscall\n", policy="RS")
    add("count_fd_write", "mov $1,%eax\nmov $COUNT_FD,%edi\nlea text(%rip),%rsi\n"
        "mov $1,%edx\nsyscall\n", policy="RS", suffix='.section .rodata\ntext: .ascii "X"\n')
    add("count_fd_truncate", "mov $77,%eax\nmov $COUNT_FD,%edi\nxor %esi,%esi\nsyscall\n",
        policy="RS")
    add("segfault", "xor %eax,%eax\nmov (%rax),%eax\n", code=-11)
    add("sigill", "ud2\n", code=-4)
    add("timeout", "loop: jmp loop\n", timeout=True)
    return cases


def limits():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run_case(args, case, order, tb_size):
    name = f"{case['name']}-{order}-tb{tb_size}"
    root = args.output_dir / name
    root.mkdir()
    report = root / "count.json"
    policy_report = root / "policy.report"
    with report.open("x+b") as count, policy_report.open("x+b") as policy:
        source = case["source"].replace("COUNT_FD", str(count.fileno()))
        asm, executable = root / "fixture.S", root / "fixture"
        asm.write_text(source)
        subprocess.run([args.cc, "-nostdlib", "-static", "-no-pie", "-Wl,-z,noexecstack",
                        str(asm), "-o", str(executable)], check=True, capture_output=True)
        plugins = [str(args.runtime / "cpu-policy.so")
                   + f",{case['feature']}=on,report-fd={policy.fileno()}",
                   f"{args.plugin},report-fd={count.fileno()}"]
        if order == "counter-first":
            plugins.reverse()
        command = [str(args.runtime / "qemu-x86_64"), "-B", "0x10000000000",
                   "-R", "4G", "-cpu", "qemu64", "-tb-size", str(tb_size)]
        for plugin in plugins:
            command.extend(("-plugin", plugin))
        command.append(str(executable))
        process = subprocess.Popen(command, pass_fds=(count.fileno(), policy.fileno()),
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, preexec_fn=limits)
        killed = False
        try:
            stdout, stderr = process.communicate(timeout=0.5 if case["timeout"] else 10)
        except subprocess.TimeoutExpired:
            killed = True
            process.kill()
            stdout, stderr = process.communicate(timeout=5)
        pid, exit_code = process.pid, process.returncode
    raw = report.read_bytes()
    status = policy_report.read_text()
    observed_exit = exit_code if exit_code >= 0 else 0
    # The production caller must NOT accept after a forced stop. This test also
    # checks that the raw collector cannot accidentally accept its empty or
    # invalid record even if passed a placeholder normal status.
    collected = subprocess.run([str(args.collector_bin), "collect-qemu", "--input", str(report),
        "--root-pid", str(pid), "--exit-code", str(observed_exit)],
        capture_output=True, text=True, check=False, timeout=10)
    result = json.loads(collected.stdout)
    okay = status == case["policy"] and not stdout
    if case["timeout"]:
        okay &= killed and exit_code == -9 and collected.returncode == 1 and not raw
    elif case["policy"] in ("RV", "RS"):
        okay &= not killed and exit_code == 126 and collected.returncode == 1 and not raw
    elif case["code"] < 0:
        okay &= not killed and exit_code == case["code"] and collected.returncode == 1
    else:
        okay &= (not killed and exit_code == case["code"] and collected.returncode == 0
                 and result["valid"] is True and result["complete"] is True
                 and result["instructions"] == case["count"])
    if collected.returncode:
        okay &= result["valid"] is False and result["complete"] is False and "instructions" not in result
    okay &= (result["measurement_contract"]["backend"] == "qemu_tcg"
             and result["measurement_contract"]["trusted_verdict_eligible"] is False
             and result["rep_telemetry_status"] == "unavailable")
    (root / "stderr.txt").write_bytes(stderr)
    (root / "collected.json").write_text(collected.stdout)
    return dict(name=name, okay=bool(okay), pid=pid, exit_code=exit_code, killed=killed,
                policy=status, raw=raw.decode(errors="replace"), result=result,
                fixture_sha256=digest(executable), command=command)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--plugin", type=Path, required=True)
    parser.add_argument("--collector-bin", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--cc", default="cc")
    args = parser.parse_args()
    for name in ("runtime", "plugin", "collector_bin", "output_dir"):
        setattr(args, name, getattr(args, name).resolve())
    args.output_dir.mkdir(parents=True, exist_ok=False)
    inputs = [args.runtime / "qemu-x86_64", args.runtime / "cpu-policy.so", args.plugin,
              args.collector_bin, Path(__file__)]
    manifest = {str(path): digest(path) for path in inputs}
    results = []
    for case in fixtures():
        for order in ("policy-first", "counter-first"):
            results.append(run_case(args, case, order, 16))
    # Translation-cache size must not turn emulator work into guest work.
    for name in ("scalar", "loop", "rep_stos_131074", "jit_scalar"):
        case = next(case for case in fixtures() if case["name"] == name)
        results.append(run_case(args, case, "policy-first", 1))
    (args.output_dir / "results.json").write_text(json.dumps(
        dict(inputs=manifest, cases=results), indent=2) + "\n")
    for result in results:
        print(("PASS " if result["okay"] else "FAIL ") + result["name"])
        if not result["okay"]:
            print(json.dumps(result))
    passed = sum(result["okay"] for result in results)
    print(f"{passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
