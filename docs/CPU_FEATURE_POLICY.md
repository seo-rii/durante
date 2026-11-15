# Native CPU feature restrictions

Durante can count native application work and stop selected instruction families
in the same DynamoRIO execution. No emulator or second measurement run is used.
The policy is optional; omitted options preserve the existing instrumentation
and lifecycle format.

```sh
durante run --drrun /opt/durante/dynamorio/bin64/drrun \
  --client /opt/durante/lib/libdurante.so --report result.json \
  --disallow-cpu-feature avx -- /path/to/program args
```

`isolate` accepts the same option. Repeat it for both restrictions; unknown
values, uppercase spellings and duplicates are rejected before launch.

| Value | Execution policy |
| --- | --- |
| `avx` | Stop AVX-family instructions, including scalar AVX, AVX2, AVX-512 and opmask operations. Baseline SSE remains available. |
| `simd` | Include AVX restrictions and also stop MMX and packed SSE computation. Permit scalar SSE floating point, ordinary XMM moves and same-register XMM zeroing. |

The client classifies decoded instruction opcodes and operands while DynamoRIO
builds application blocks, then inserts termination immediately before the
forbidden instruction. Translation alone does not reject unreachable bytes.
Generated code, changed code and alternate instruction entry points are checked
when DynamoRIO translates their executed path. Followed forks and threads
inherit the selected policy. This uses the pinned runtime's
existing instruction-decoder and code-replay support.

While a policy is enabled, application `execve` and `execveat` calls return
`EPERM`, including normal pathname, file-descriptor and compatibility-image
transitions. The pinned runtime cannot guarantee policy instrumentation across
every replacement image. The initially selected program still launches normally.
Unsupported clone forms and x32 syscalls also return `EPERM`; compatibility
syscall instructions such as `int 0x80` stop before execution and leave the
measurement unavailable. These guards do not invent an AVX or SIMD violation.
Omitting the policy retains the existing measurement-only syscall behavior.

The check includes application libraries, not only the submitted source. A
scalar program can therefore be stopped by packed SIMD in libc or another
library. A CPU-dispatch environment preference can reduce such use but is not
an enforcement mechanism. The client and DynamoRIO's own native instructions
are not application instructions and do not receive the application policy.

## Embedding

An existing execution owner can pass the client options directly:

```sh
drrun -quiet -follow_children -disable_traces -vm_size 32M -vmheap_size 32M \
  -c /trusted/libdurante.so -outdir /private/new-records -rep_bytes \
  -disallow_cpu_feature avx -- /path/to/program args
```

The directory and completed-tree collection rules in [PROTOCOL.md](PROTOCOL.md)
still apply. A requested policy needs matching metadata from every image;
absence of a capability receipt must not silently satisfy an enabled policy.
`run` and `isolate` verify that the returned policy matches their options.

A violation records `cpu_feature_violation` with `feature: "avx"` or `"simd"`
and immediately exits that process with code 126 before the instruction. The
collector returns `valid: false`, `complete: false`, no partial work totals and
the exact reason `disallowed CPU feature: avx` or `disallowed CPU feature: simd`.
When both policies are enabled, an AVX-family violation is attributed to `avx`.
An ordinary application exit 126 does not imply a policy violation and can have
a complete measurement. The outer owner must retain its normal deadlines and
full-tree cleanup even when a child process is stopped for a violation.

Successful enabled runs add canonical `disallowed_cpu_features` to their
aggregate, for example `["avx", "simd"]`. Their metric remains
`bb_entry_app_instructions`; REP accounting and its quality semantics are
unchanged. No time-conversion weights or resource budgets are changed by the
policy. Runtime and instrumentation overhead still consumes actual resources.

## Trust and limits

This option has Durante's existing in-process trust level. Application code,
instrumentation and counters share an address space, and application-writable
records are not an authenticated enforcement receipt. A hostile program may
tamper with those facilities. Results retain `diagnostic_only`,
`integrity_verified: false` and `trusted_verdict_eligible: false`.

The option does not replace host isolation, CPU/RSS/output limits or an external
process-tree owner. It does not turn diagnostic instruction counts into trusted
judge limits. See [SECURITY.md](../SECURITY.md).

`tests/native_cpu_policy.py` runs real release/debug fixtures for the selected
instruction families, generated code, lifecycle inheritance, rejected execution
transitions and memory effects.
The collector protocol vectors and `tests/native_policy_cli.rs` cover policy
agreement, violation reporting, legacy clients and ordinary nonzero exits.
