#!/usr/bin/env python3
"""Qualify native pre-execution SIMD/AVX blocking and unchanged work semantics."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

from concurrent_spawn import collector, cleanup_orphans, own_subreaper
from runtime_compat import run_process

ROOT = Path(__file__).resolve().parents[1]
TOTALS = ("instructions", "rep_bytes", "rep_entries", "unsupported_rep_entries")


def fixtures():
    cases = []

    def add(name, body="", *, features=("avx",), violation=None, count=None,
            code=0, suffix="", arguments=(), marker=None, cli=False,
            violation_count=1, unavailable=False, rep_bytes=None, stdout="",
            starts=None, spawns=None, exec_attempts=None, debug_failure=None):
        source = (".global _start\n.text\n_start:\n" + body
                  + f"mov $60,%eax\nmov ${code},%edi\nsyscall\n" + suffix
                  + '\n.section .note.GNU-stack,"",@progbits\n')
        cases.append(dict(name=name, source=source, features=list(features), violation=violation,
                          count=count, code=code, arguments=arguments, marker=marker, cli=cli,
                          violation_count=violation_count if violation else 0,
                          unavailable=unavailable, rep_bytes=rep_bytes, stdout=stdout,
                          starts=starts, spawns=spawns, exec_attempts=exec_attempts,
                          debug_failure=debug_failure))

    add("scalar", count=3, cli=True)
    add("scalar_no_policy", count=3, features=())
    add("normal126", count=3, code=126, cli=True)
    add("both_policy", count=3, features=("simd", "avx"), cli=True)
    add("scalar_sse", "pxor %xmm0,%xmm0\nxorps %xmm1,%xmm1\nxorpd %xmm2,%xmm2\n"
        "addsd %xmm0,%xmm1\n", features=("simd",), count=7)
    add("packed_sse_allowed", "addps %xmm0,%xmm1\n", count=4)
    add("packed_sse", "addps %xmm0,%xmm1\n", features=("simd",), violation="simd", code=126)
    add("packed_xor", "pxor %xmm0,%xmm1\n", features=("simd",), violation="simd", code=126)
    add("mmx", "movq %rax,%mm0\n", features=("simd",), violation="simd", code=126)
    add("emms", "emms\n", features=("simd",), violation="simd", code=126)
    add("avx_raw", ".byte 0xc5,0xf8,0x77\n", violation="avx", code=126, cli=True)
    add("avx_scalar", "vaddsd %xmm0,%xmm1,%xmm2\n", violation="avx", code=126)
    add("avx_simd", ".byte 0xc5,0xf8,0x77\n", features=("simd",), violation="simd", code=126)
    add("avx_both", ".byte 0xc5,0xf8,0x77\n", features=("simd", "avx"), violation="avx", code=126)
    add("unreached_avx", "jmp skip\n.byte 0xc5,0xf8,0x77\nskip:\n", count=4)
    add("unreached_simd", "jmp skip\naddps %xmm0,%xmm1\nskip:\n", features=("simd",), count=4)
    for name, instruction, features in (
            ("translated_unreached_avx", "vzeroupper", ("avx",)),
            ("translated_unreached_simd", "addps %xmm0,%xmm1", ("simd",))):
        # The load faults before a forbidden instruction in the same translated
        # block. Its handler exits successfully, so translation must not reject.
        add(name, "mov $13,%eax\nmov $11,%edi\nlea action(%rip),%rsi\n"
            "xor %edx,%edx\nmov $8,%r10d\nsyscall\nxor %eax,%eax\n"
            "mov (%rax),%rax\n" + instruction + "\n", features=features,
            suffix=".text\nhandler: mov $60,%eax\nxor %edi,%edi\nsyscall\n"
                   "restorer: mov $15,%eax\nsyscall\n.data\n.p2align 3\n"
                   "action: .quad handler,0x04000000,restorer,0\n")
    add("unsupported_amx", "tilerelease\n", features=("simd",), code=-4, unavailable=True)
    add("unsupported_amx_no_policy", "tilerelease\n", features=(), code=-4, unavailable=True)
    add("interior_avx", "lea immediate+2(%rip),%rax\njmp *%rax\n", violation="avx", code=126,
        suffix=".text\nimmediate: .byte 0x48,0xb8,0xc5,0xf8,0x77,0xc3,0,0,0,0\n")
    jit = ("mov $9,%eax\nxor %edi,%edi\nmov $4096,%esi\nmov $7,%edx\n"
           "mov $0x22,%r10d\nmov $-1,%r8\nxor %r9d,%r9d\nsyscall\n")
    add("jit_scalar", jit + "movl $0x9090c390,(%rax)\ncall *%rax\n", count=15)
    add("jit_avx", jit + "movl $0xc377f8c5,(%rax)\ncall *%rax\n", violation="avx", code=126)
    add("jit_simd", jit + "movl $0xc3c8580f,(%rax)\ncall *%rax\n",
        features=("simd",), violation="simd", code=126)
    add("rewrite_avx", "call victim\nlea victim(%rip),%r12\nmov %r12,%rdi\n"
        "and $-4096,%rdi\nmov $4096,%esi\nmov $7,%edx\nmov $10,%eax\nsyscall\n"
        "movl $0xc377f8c5,(%r12)\ncall *%r12\n", violation="avx", code=126,
        suffix=".text\nvictim: ret\nnop\nnop\nnop\n")
    for syscall, name in ((57, "fork"), (58, "vfork")):
        add(name, f"mov ${syscall},%eax\nsyscall\ntest %eax,%eax\njz child\n"
            "mov %eax,%edi\nmov $61,%eax\nlea status(%rip),%rsi\nxor %edx,%edx\n"
            "xor %r10d,%r10d\nsyscall\njmp done\nchild:\n.byte 0xc5,0xf8,0x77\n"
            "mov $60,%eax\nmov $37,%edi\nsyscall\ndone:\n", violation="avx",
            suffix=".bss\n.p2align 3\nstatus: .skip 8\n")
    add("vfork_parent", "mov $58,%eax\nsyscall\ntest %eax,%eax\njz child\n"
        "mov %eax,%edi\nmov $61,%eax\nlea status(%rip),%rsi\nxor %edx,%edx\n"
        "xor %r10d,%r10d\nsyscall\n.byte 0xc5,0xf8,0x77\nchild:\n"
        ".byte 0xc5,0xf8,0x77\n", violation="avx", code=126, violation_count=2,
        suffix=".bss\n.p2align 3\nstatus: .skip 8\n")
    add("exec_guard", "mov 16(%rsp),%rdi\nxor %edx,%edx\npush %rdx\npush %rdi\n"
        "mov %rsp,%rsi\nmov $59,%eax\nsyscall\ncmp $-1,%rax\njne exec_failed\n",
        arguments=("@avx_raw",), starts=1, spawns=0, exec_attempts=0, cli=True,
        suffix=".text\nexec_failed: mov $60,%eax\nmov $77,%edi\nsyscall\n")
    add("thread", "mov $56,%eax\nmov $0x50f00,%edi\nlea stack_end(%rip),%rsi\n"
        "xor %edx,%edx\nxor %r10d,%r10d\nxor %r8d,%r8d\nsyscall\n"
        "test %eax,%eax\njz child\nmov $35,%eax\nlea delay(%rip),%rdi\n"
        "xor %esi,%esi\nsyscall\njmp done\nchild:\n.byte 0xc5,0xf8,0x77\n"
        "mov $60,%eax\nxor %edi,%edi\nsyscall\ndone:\n", violation="avx", code=126,
        suffix=".data\n.p2align 3\ndelay: .quad 1,0\n.bss\n.p2align 4\n.skip 65536\nstack_end:\n")
    add("signal_handler", "mov $13,%eax\nmov $4,%edi\nlea action(%rip),%rsi\n"
        "xor %edx,%edx\nmov $8,%r10d\nsyscall\nud2\n", violation="avx", code=126,
        suffix=".text\nhandler: .byte 0xc5,0xf8,0x77\nmov $60,%eax\nmov $113,%edi\nsyscall\n"
               "restorer: mov $15,%eax\nsyscall\n.data\n.p2align 3\n"
               "action: .quad handler,0x04000000,restorer,0\n")
    # Shared mapping is inspected by the host after exit. The allowed policy
    # control must change all bytes, proving that a blocked zero is observable.
    setup = ("mov 16(%rsp),%rdi\nmov $2,%eax\nmov $2,%esi\nxor %edx,%edx\nsyscall\n"
             "mov %eax,%r8d\nmov $9,%eax\nxor %edi,%edi\nmov $4096,%esi\n"
             "mov $3,%edx\nmov $1,%r10d\nxor %r9d,%r9d\nsyscall\nmov %rax,%rdi\n"
             "movdqa ones(%rip),%xmm0\nmovdqa ones(%rip),%xmm1\n")
    ones = ".section .rodata\n.p2align 4\nones: .fill 16,1,255\n"
    add("simd_store_control", setup + "maskmovdqu %xmm1,%xmm0\n", suffix=ones, marker=255)
    add("simd_store_blocked", setup + "maskmovdqu %xmm1,%xmm0\n", suffix=ones,
        marker=0, features=("simd",), violation="simd", code=126)
    add("avx_store_blocked", setup + "vmovups %xmm0,(%rdi)\n", suffix=ones,
        marker=0, violation="avx", code=126)
    add("rep_with_policy", "lea buffer(%rip),%rdi\nmov $16,%ecx\nxor %eax,%eax\nrep stosb\n",
        features=("simd",), count=7, rep_bytes=16, suffix=".bss\nbuffer: .skip 16\n")

    child_message = "FORBIDDEN_CHILD\n"
    child_write = ("mov $1,%eax\nmov $1,%edi\nlea child_message(%rip),%rsi\n"
                   "mov $16,%edx\nsyscall\n")
    child_data = '.section .rodata\nchild_message: .ascii "FORBIDDEN_CHILD\\n"\n'
    add("transition_child", "paddq %xmm0,%xmm1\n" + child_write,
        features=(), stdout=child_message, suffix=child_data)
    failed = ".text\nfailed: mov $60,%eax\nmov $77,%edi\nsyscall\n"
    denied = "cmp $-1,%rax\njne failed\n"
    execveat = ("mov 16(%rsp),%rdi\nmov %rdi,%r12\nmov $2,%eax\n"
                "xor %esi,%esi\nxor %edx,%edx\nsyscall\nmov %eax,%edi\n"
                "lea empty(%rip),%rsi\nxor %r10d,%r10d\npush %r10\npush %r12\n"
                "mov %rsp,%rdx\nmov $0x1000,%r8d\nmov $322,%eax\nsyscall\n")
    for policy in (True, False):
        add("execveat_guard" if policy else "execveat_default", execveat + denied,
            features=("simd",) if policy else (), arguments=("@transition_child",),
            stdout="" if policy else child_message, unavailable=not policy,
            starts=1, spawns=0, exec_attempts=0 if policy else 1,
            suffix=failed + '.section .rodata\nempty: .byte 0\n')
    for kind, argv in (("null", "xor %esi,%esi\n"),
                       ("empty", "push %rdx\nmov %rsp,%rsi\n"),
                       ("empty_arg0", "push %rdx\nlea empty(%rip),%rax\npush %rax\nmov %rsp,%rsi\n")):
        body = "mov 16(%rsp),%rdi\nxor %edx,%edx\n" + argv + "mov $59,%eax\nsyscall\n"
        add("execve_" + kind + "_guard", body + denied,
            features=("simd",), arguments=("@transition_child",), starts=1, spawns=0,
            exec_attempts=0, suffix=failed + '.section .rodata\nempty: .byte 0\n')
        if kind == "null":
            add("execve_null_default", body + denied, features=(), arguments=("@transition_child",),
                stdout=child_message, unavailable=True, exec_attempts=1, suffix=failed)
    # Compatibility execve uses a low-address path and argv, both genuinely
    # accepted by the kernel. Policy mode must stop before the int instruction.
    compat = ("mov 16(%rsp),%rsi\nlea compat_path(%rip),%rdi\n"
              "copy_path: lodsb\nstosb\ntest %al,%al\njnz copy_path\n"
              "mov $11,%eax\nlea compat_path(%rip),%rbx\nlea compat_argv(%rip),%rcx\n"
              "xor %edx,%edx\nint $0x80\n")
    compat_data = (".bss\ncompat_path: .skip 4096\n.data\n.p2align 2\n"
                   "compat_argv: .long compat_path,0\n")
    add("compat_exec_guard", compat + child_write, features=("simd",), code=126,
        arguments=("@transition_child",), unavailable=True, starts=1, spawns=0,
        exec_attempts=0, suffix=compat_data + child_data)
    add("compat_exec_default", compat, features=(), arguments=("@transition_child",),
        stdout=child_message, unavailable=True, suffix=compat_data,
        debug_failure="core/vmareas.c:6214 !dynamo_vm_area_overlap(base, base + size)")
    add("sysenter_guard", ".byte 0x0f,0x34\n" + child_write, features=("simd",), code=126,
        unavailable=True, starts=1, spawns=0, exec_attempts=0, suffix=child_data,
        debug_failure="core/arch/arch.c:3008 instr_get_opcode(&instr) == OP_jmp_short || instr_get_opcode(&instr) == OP_int")
    add("x32_guard", "mov $0x40000208,%eax\nxor %edi,%edi\nxor %esi,%esi\n"
        "xor %edx,%edx\nsyscall\n" + denied, features=("simd",), starts=1, spawns=0,
        exec_attempts=0, suffix=failed)
    for name, flags in (("shared_vm", 0x111), ("shared_files", 0x411)):
        add("clone_" + name + "_guard", f"mov $56,%eax\nmov ${flags},%edi\n"
            "lea stack_end(%rip),%rsi\nxor %edx,%edx\nxor %r10d,%r10d\n"
            "xor %r8d,%r8d\nsyscall\ntest %eax,%eax\njz escaped_child\n" + denied,
            features=("simd",), starts=1, spawns=0, exec_attempts=0,
            suffix=failed + ".text\nescaped_child: paddq %xmm0,%xmm1\n" + child_write
                   + "mov $60,%eax\nxor %edi,%edi\nsyscall\n" + child_data
                   + ".bss\n.p2align 4\n.skip 65536\nstack_end:\n")
    add("clone3_flags_guard", "mov $435,%eax\nlea clone_args(%rip),%rdi\nmov $88,%esi\n"
        "syscall\n" + denied, features=("simd",), starts=1, spawns=0, exec_attempts=0,
        suffix=failed + ".data\n.p2align 3\nclone_args: .quad 0x100000000\n.skip 80\n")
    for name, child_body, unavailable, child_args in (
            ("nested_spawn", "mov $57,%eax\nsyscall\n" + denied, False, ()),
            ("compat", "mov $20,%eax\nint $0x80\n" + child_write, True, ()),
            ("exec", "mov 16(%rsp),%rdi\nxor %edx,%edx\nxor %esi,%esi\n"
             "mov $59,%eax\nsyscall\n" + denied, False, ("@transition_child",))):
        status = 126 << 8 if unavailable else 0
        add("vfork_" + name + "_guard", "mov $58,%eax\nsyscall\ntest %eax,%eax\njz child\n"
            "mov %eax,%edi\nmov $61,%eax\nlea status(%rip),%rsi\nxor %edx,%edx\n"
            f"xor %r10d,%r10d\nsyscall\ncmpl ${status},status(%rip)\njne failed\n"
            "jmp done\nchild:\n" + child_body
            + "mov $60,%eax\nxor %edi,%edi\nsyscall\ndone:\n", features=("simd",),
            unavailable=unavailable, starts=2, spawns=1, exec_attempts=0, arguments=child_args,
            suffix=failed + child_data + ".bss\n.p2align 3\nstatus: .skip 8\n")
    return cases


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def check_measurement(measurement, case):
    if case["unavailable"]:
        return (measurement.get("valid") is False and measurement.get("complete") is False
                and not any(name in measurement for name in TOTALS))
    if case["violation"]:
        return (measurement.get("valid") is False and measurement.get("complete") is False
                and measurement.get("reason") == "disallowed CPU feature: " + case["violation"]
                and not any(name in measurement for name in TOTALS))
    return (measurement.get("valid") is True and measurement.get("complete") is True
            and measurement.get("disallowed_cpu_features", []) == sorted(case["features"])
            and (case["rep_bytes"] is None or measurement.get("rep_bytes") == case["rep_bytes"])
            and (case["count"] is None or measurement.get("instructions") == case["count"]))


def execute(args, case, binaries, core="release", cli=False):
    directory = args.output_dir / (case["name"] + ("-cli" if cli else "-" + core))
    directory.mkdir()
    records = directory / "records"
    target = [str(binaries[case["name"]]), *[
        str(binaries[arg[1:]]) if arg.startswith("@") else arg for arg in case["arguments"]]]
    marker = directory / "marker"
    if case["marker"] is not None:
        marker.write_bytes(bytes(4096))
        target.append(str(marker))
    if cli:
        report = directory / "report.json"
        command = [str(args.runner_bin), "run", "--drrun", str(args.sdk / "bin64/drrun"),
                   "--client", str(args.client), "--report", str(report),
                   "--records-dir", str(records), "--timeout-ms", "10000"]
        for feature in case["features"]:
            command += ["--disallow-cpu-feature", feature]
    else:
        records.mkdir()
        command = [str(args.sdk / "bin64/drrun"), *(["-debug"] if core == "debug" else []),
                   "-quiet", "-follow_children", "-disable_traces", "-vm_size", "32M",
                   "-vmheap_size", "32M"]
        command += ["-c", str(args.client), "-outdir", str(records), "-rep_bytes"]
        for feature in case["features"]:
            command += ["-disallow_cpu_feature", feature]
    command += ["--", *target]
    row = run_process(command, directory, 15000)
    row.update(name=case["name"], core=core, cli=cli)
    # These two pinned debug-core failures were reproduced with the pre-guard
    # client. They already stop execution and yield unavailable measurements.
    # Require their precise diagnostics instead of accepting arbitrary errors.
    debug_failure = case["debug_failure"] if core == "debug" and not cli else None
    expected_code = 255 if debug_failure else case["code"]
    expected_stdout = "" if debug_failure else case["stdout"]
    pid = row["root_pid"]
    if cli:
        envelope = json.loads(report.read_text()) if report.is_file() else {}
        measurement = envelope.get("measurement", {})
        execution = envelope.get("execution", {})
        pid = execution.get("root_pid", 0)
        okay = (row["exit_code"] == (1 if case["violation"] or case["unavailable"] else 0)
                and execution.get("exit_code") == case["code"]
                and execution.get("cleanup_complete") is True)
    else:
        result = subprocess.run([str(args.runner_bin), "collect", "--dir", str(records),
            "--root-pid", str(pid)], capture_output=True, text=True, timeout=10, check=False)
        measurement = json.loads(result.stdout)
        okay = (result.returncode == (1 if case["violation"] or case["unavailable"] else 0)
                and row["exit_code"] == expected_code)
    events = [json.loads(line) for path in sorted(records.glob("process-*.jsonl"))
              for line in path.read_text().splitlines()]
    starts = [event for event in events if event["event"] == "start"]
    violations = [event for event in events if event["event"] == "cpu_feature_violation"]
    expected_policy = ",".join(sorted(case["features"]))
    for event in starts:
        okay &= ((event.get("cpu_policy_version") == 1 and event.get("cpu_policy") == expected_policy)
                 if expected_policy else "cpu_policy" not in event and "cpu_policy_version" not in event)
    okay &= bool(starts) and len(violations) == case["violation_count"]
    for key, event_name in (("starts", "start"), ("spawns", "spawn"),
                            ("exec_attempts", "exec_attempt")):
        if case[key] is not None:
            okay &= sum(event["event"] == event_name for event in events) == case[key]
    okay &= all(event["feature"] == case["violation"] for event in violations)
    okay &= check_measurement(measurement, case) and measurement == collector.collect(records, pid)
    okay &= row["cleanup_complete"] and not row["timeout"] and row["stdout"] == expected_stdout
    if debug_failure:
        okay &= debug_failure in row["stderr"] and "Internal Error: DynamoRIO debug check failure" in row["stderr"]
    else:
        okay &= "Internal Error: DynamoRIO" not in row["stderr"]
    if case["marker"] is not None:
        observed = marker.read_bytes()[:16]
        okay &= observed == bytes([case["marker"]]) * 16
        row["marker_hex"] = observed.hex()
    row.update(passed=bool(okay), measurement=measurement, events=events,
               fixture_sha256=sha(binaries[case["name"]]))
    (directory / "result.json").write_text(json.dumps(row, indent=2) + "\n")
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("sdk", "client", "runner-bin", "output-dir"):
        parser.add_argument("--" + flag, type=Path, required=True)
    args = parser.parse_args()
    for name in ("sdk", "client", "runner_bin", "output_dir"):
        setattr(args, name, getattr(args, name).resolve())
    args.output_dir.mkdir(parents=True, exist_ok=False)
    cases, binaries = fixtures(), {}
    for case in cases:
        source = args.output_dir / (case["name"] + ".S")
        executable = args.output_dir / case["name"]
        source.write_text(case["source"])
        subprocess.run(["gcc", "-static", "-nostdlib", "-no-pie", "-Wl,-z,noexecstack",
                        str(source), "-o", str(executable)], check=True, capture_output=True)
        binaries[case["name"]] = executable
    inputs = [ROOT / "client/durante.c", ROOT / "client/cpu_policy.h", Path(__file__), args.client,
              args.runner_bin, args.sdk / "bin64/drrun",
              args.sdk / "lib64/release/libdynamorio.so", args.sdk / "lib64/debug/libdynamorio.so"]
    provenance = {str(path): sha(path) for path in inputs}
    rows = []
    libc, previous = own_subreaper()
    try:
        for core in ("release", "debug"):
            for case in cases:
                row = execute(args, case, binaries, core)
                rows.append(row)
                print(("PASS " if row["passed"] else "FAIL ") + core + "/" + case["name"], flush=True)
        for case in cases:
            if case["cli"]:
                row = execute(args, case, binaries, cli=True)
                rows.append(row)
                print(("PASS " if row["passed"] else "FAIL ") + "cli/" + case["name"], flush=True)
    finally:
        cleaned = cleanup_orphans()
        libc.prctl(36, previous, 0, 0, 0)
    stable = provenance == {str(path): sha(path) for path in inputs}
    passed = sum(row["passed"] for row in rows)
    (args.output_dir / "results.json").write_text(json.dumps(
        dict(inputs=provenance, source_unchanged=stable, cleanup_complete=cleaned, rows=rows), indent=2) + "\n")
    print(f"{passed}/{len(rows)} passed")
    return 0 if passed == len(rows) and cleaned and stable else 1


if __name__ == "__main__":
    raise SystemExit(main())
