# Generated-code lifecycle qualification

`tests/jit_lifecycle.py` compares fixed static assembly under native execution,
release/debug engine-only controls, release/debug counter modes with REP off/on,
and the Rust supervisor. Each program installs a function returning 42, executes
it ten times, replaces it with a function returning 43, then executes the
replacement ten times. A stale cached function fails the output oracle.

Cases exercise RW-to-RX permission transitions, explicit unmap/remap at the same
owned address, and writes to a previously executed RWX page. Each has a zero-NOP
and a 32-NOP replacement. An independent `objdump` count of the static success
path, fixed loop multiplicities and generated bodies determines the exact
instruction oracle. The two REP copies have known byte lengths.

## Unresolved RWX duplicate accounting

Protection transitions and remapping satisfy the exact oracles. **RWX rewrite
remains known-unqualified. These checks reproduce the accounting error; they do
not fix the counter.** The application outputs the correct result and exits 0, while
a structurally valid, `uninterrupted` measurement reports 172 rather than 169
instructions, 18 rather than 12 REP bytes, and three rather than two REP entries.
With 32 extra NOPs, observed/expected values are 492/489 instructions and 82/44 REP
bytes. Release and debug cores reproduce the same discrepancy.

The engine handles a modified-code fault internally before its public signal
callback, so entry instrumentation can observe work again without an application
signal. The pinned runtime's restore-state event also serves PC-only queries;
its registers need not describe an application write address. Neither
`restore_memory == false` nor a memory-writing opcode proves replay. Broad
client-side filters were rejected because they invalidate otherwise passing
Node and Java fixtures. No speculative filter is included.

A precise runtime notification would need to distinguish retrying the write
inside the existing cache from dispatch re-entry after a mid-fragment selfmod
exit. Those decisions are internal to `check_for_modified_code` and
`handle_modified_code` in the pinned DynamoRIO revision
`6847bb210bea0ca11d1bf47704383d6a075da490`; the public event API exposes no such
notification. Resolving this requires a reviewed runtime hook or a replay-aware
accounting design, with ordinary runtime compatibility retained.

## Reproduction and qualification policy

After building the pinned SDK, client and CLI:

```sh
python3 tests/jit_lifecycle.py --sdk toolkit/sdk/DynamoRIO-Linux-11.3.0-1 \
  --client build/libdurante.so --runner-bin target/release/durante \
  --output-dir .results/jit-lifecycle \
  --require-qualified-mode protect --require-qualified-mode remap
```

Use a fresh output directory, Linux x86-64, Python 3.11+, GCC, binutils `objdump`
and `prlimit`. Setup tools and each invocation have wall, CPU, address-space and
output-file bounds. A subreaper owns and cleans adopted descendants. The same
recorded absolute limiter path is used throughout. Source, tool, runtime,
client and generated fixture identities are checked before/after execution.
These checks are reproducibility evidence, not hostile-host attestation.
The debug launcher flag precedes engine options. Direct engine invocations use
the release default's critical/error stderr mask (`12`) to suppress informational
debug banners; any remaining stderr is a failure, including ignored-option errors.

The matrix records 48 observations: 18 functional controls, 20 qualified counter
cases for protection transitions/remapping, and 10 known-unqualified RWX cases.
Only that exact accounting discrepancy can receive `known_unqualified`; it
cannot hide a crash, invalid/missing/truncated records, different counts, output
change, failed cleanup or changed inputs. Corrected RWX counts can become
`qualified`. The raw observations and qualification errors stay in `results.json`.

Default exit 0 means this stated policy was satisfied, not that RWX is supported.
`qualified_cases` excludes controls and known-unqualified cases. To demand RWX
qualification as well, use a separate fresh directory and add:

```sh
python3 tests/jit_lifecycle.py --sdk toolkit/sdk/DynamoRIO-Linux-11.3.0-1 \
  --client build/libdurante.so --runner-bin target/release/durante \
  --output-dir .results/jit-lifecycle-strict --require-qualified-mode rwx-rewrite
```

The current client returns harness exit 1 with `required_unqualified` containing
`rwx-rewrite`. The suite retains this failure for a future precise fix. The CLI
itself currently still accepts those structurally complete, duplicated counts;
consumers must not infer RWX qualification from a valid counter report.

These single-threaded fixtures do not qualify concurrent code patching, runtime
GC/deoptimization, a particular JVM/Node JIT compilation, or `isolate`. Counts
remain diagnostic and application-mutable, with no trusted CPU-time conversion.
