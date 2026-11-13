# Signal vector and floating-point state qualification

`tests/signal_state.py` compares native execution, DynamoRIO alone, and Durante
with REP telemetry off/on. Every engine/client case runs against both the release
and debug cores from the same SDK. CI requires the SSE2 cases; AVX2 and AVX-512
execute only when a native CPUID/XGETBV probe confirms CPU and OS support.
Unavailable ISA rows are explicit skips. A failed engine or client case is a
failure, even when its counters are structurally complete.

The fixture establishes application state in assembly, observes it in a signal
frame, deliberately clobbers the handler's live state, then verifies what
`sigreturn` restores. The C handler is compiled with `-mgeneral-regs-only` so
compiler-generated floating-point/vector operations cannot replace the state
being tested. It tests these registers and arithmetic properties:

| Case | State and oracle |
| --- | --- |
| SSE2 | All 128 bits of XMM15 in the signal frame and after return. |
| AVX2 | All 256 bits of YMM15, including the XSAVE upper half. |
| AVX-512F | All 512 bits of ZMM31 and the K7 mask register. |
| Every ISA | x87 control word, precision status, nonempty stack and ST(0)=1.5; MXCSR rounding and precision status. Downward rounding produces 1; upward rounding produces 2, using both x87 and SSE conversion instructions. |

There are two delivery cases. `fault` deliberately reads address zero; its handler
checks the original context and moves RIP past a subsequent register clobber.
`async` starts a one-shot five-millisecond timer while SIGALRM is blocked,
establishes register state, then unblocks it and spins for a bounded number of
iterations. Delivery may occur on the unblocking syscall's return or during that
loop. It does not assume a particular instruction address or instruction total.

Both outer handlers must execute on the configured 128 KiB alternate stack.
Each establishes a different vector pattern and upward rounding before sending
SIGUSR2 to its own thread. The nested handler verifies this second context and
its alternate-stack address, clobbers the live registers/FPU, and returns. The
outer handler checks the restored nested state before returning to the original
application state. Exactly two handler invocations, two frame checks, two stack
checks, and both state-restoration checks must succeed.

Linux x86-64 signal frames use the FXSAVE prefix and, when present, an XSAVE
extension. The fixture follows the layouts in `asm/sigcontext.h` and
`sys/ucontext.h`: it validates the extension magic values, bounded size and
standard-format feature masks. Extended-component offsets and sizes come from
CPUID leaf 0xD. It does not assume a fixed AVX-512 offset or inspect a missing
component as if it were present. Unsupported future frame formats fail the
fixture and need separate qualification.

The fixture exposed an upstream runtime defect on hosts whose kernel signal
frames contain extra XSAVE state. When preparing a delayed or nested signal,
the runtime saved x87 state with FXSAVE, cleared live exceptions with FNCLEX,
then overwrote the saved state with an x87-only XSAVE. The precision status bit
was lost even though arithmetic and vector results could remain correct.
The pinned runtime patch now copies the original legacy FXSAVE prefix after
that XSAVE. It keeps the early exception clear and preserves the separate
XMM/YMM/ZMM, mask-register and XSAVE-header restoration. The strict status-bit
assertions remain enabled for every supported ISA.

## Reproduce and inspect

Build the SDK, client and release CLI as described in the repository README,
then use a fresh output directory:

```sh
python3 tests/signal_state.py --sdk toolkit/sdk/DynamoRIO-Linux-11.3.0-1 \
  --client build/libdurante.so --runner-bin target/release/durante \
  --output-dir .results/signal-state --require-isa sse2 --repeats 2
```

Rebuild prepared SDKs after updating the runtime patch. Its digest is recorded
in `.durante-runtime.json`; the current `isolate` validator rejects the older
patch digest even when that installation's binary hashes still match.

`--require-isa avx2` or `--require-isa avx512` makes an unavailable requested ISA
fail qualification. Repetitions are bounded to 1–5 and individual invocations to
1–20 seconds (`--timeout-ms`, default 15,000). Every execution has a 2 GiB address
space cap, a 15-second CPU cap, a 1 MiB output-file cap, and core dumps disabled.
The harness bounds captured output, terminates/reaps timed-out process trees, and
restores its subreaper setting. Fixture compilation has a 30-second deadline.

`results.json` records source, fixture and installation hashes, the native
feature probe, each command, exact stdout, bounded stderr, errors and cleanup
status. Debug-core diagnostics may appear on stderr; native/release stderr must
be empty. The harness checks source/installation hashes before and after the run. The
raw lifecycle records and Rust collector output remain in the result directory
for inspection. Rust and Python aggregates must agree. Instrumented cases require
one process/image, complete accounting, exactly two observed signals, and the
locally constructed diagnostic contract with `integrity_verified: false`.

This establishes only these fixtures on their recorded installation. It does
not qualify every asynchronous instruction boundary, signal number, vector
instruction, FPU exception, alternate-stack mode, or context-switch API. It does
not resolve the separate ignored-signal/syscall-restart limitations documented
in the README. Asynchronous delivery changes basic-block-entry work; matching
output or a complete report does not imply identical counts, hardware retirement
accuracy, tamper resistance, or a CPU-time conversion. The counter implementation
is unchanged; the runtime fix preserves application state without adjusting counts.
