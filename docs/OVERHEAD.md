# Fixed-fixture invocation overhead

`experiments/overhead.py` compares the current native executable, DynamoRIO alone,
and the Durante client with REP telemetry disabled/enabled. It is a bounded,
diagnostic experiment for Linux x86-64. It does not convert counts to CPU time,
qualify measurement integrity or establish performance for arbitrary workloads.

Build the client, SDK and Rust CLI as described in [README.md](../README.md), then:

```sh
python3 experiments/overhead.py \
  --sdk toolkit/sdk/DynamoRIO-Linux-11.3.0-1 \
  --client build/libdurante.so --runner-bin target/release/durante \
  --output-dir .results/overhead --warmups 1 --repeats 5
```

GCC and Python 3.11+ are also required. The output directory must be new; it retains
the compiled fixtures/timer, raw client records, per-invocation timer reports and
`results.json`. Existing directories or timer reports are never overwritten.
Directories are private (`0700`), result files use `0600`, and child tools inherit
a restrictive umask. No user-supplied command or variable-size workload is run.

Choose 1–3 warmups and 1–10 measured repeats per fixture/mode. The default is one
warmup plus five samples. The harness pins itself and its children to the first
allowed logical CPU; `--cpu N` selects another allowed CPU. It restores the original
affinity on completion. Variant order rotates across fixtures/repeats to reduce
fixed ordering effects. Warmups are preserved and validated but excluded from
statistics. These are repeated new processes, not a persistent warmed application.

## Fixed oracles

All fixtures are static assembly with one process, one image, no libc, threads,
child processes or generated code. They write one predictable little-endian
64-bit value. Successful native, engine and counter runs must preserve those bytes
and exit 0 without diagnostics.

| Fixture | Work | Instruction oracle | REP oracle when enabled |
| --- | --- | ---: | --- |
| `startup` | Write a constant and exit | 8 | 0 entries/bytes |
| `loop` | 2,000,000 iterations of ADD + LOOP; output 6,000,000 | 4,000,011 | 0 entries/bytes |
| `rep` | 128 rounds of STOSB + MOVSB over 64 KiB; output eight `0x5a` bytes | 1,292 | 256 entries; 16,777,216 requested bytes |

The common write/exit sequence has eight instructions. The loop adds two setup
instructions, two instructions per iteration and one result store. REP adds two
setup instructions, ten per round and two result instructions; each REP entry is
one instruction in Durante's metric, regardless of its repeat count.

The Rust collector runs after the measured child is reaped. Counter observations
must be complete, uninterrupted, single-process/image and diagnostic-only, with
the exact instruction/REP totals above, zero unsupported REP entries/signals and
the current measurement contract. Any discrepancy fails the experiment.

## What is measured

| Mode | Timed command |
| --- | --- |
| `native` | Fixed assembly executable |
| `engine` | `drrun` with the same executable and no client |
| `counter_rep_off` | `drrun` plus Durante, REP telemetry disabled |
| `counter_rep_on` | `drrun` plus Durante and `-rep_bytes` |

All engine modes use `-quiet -follow_children -vm_size 32M -vmheap_size 32M`.
Reported time includes process creation, the small launcher's child setup/exec,
runtime startup/translation, client initialization, application work, client
record writes and process exit. Wall time ends when the timer observes completion.
Compilation, Python orchestration, input hashing and Rust collection are outside
that interval. This does not measure `durante run` supervision or application-only
execution cost. Short fixtures can be dominated by startup and observation costs.

The small C timer calls `wait4` for each child and records that invocation's user
and system CPU time and Linux peak RSS in KiB. It never subtracts cumulative
`RUSAGE_CHILDREN.ru_maxrss` snapshots. A separate C process prevents Python's
pre-exec memory high-water mark from dominating the measured child's RSS. The
child still includes the small C launcher's fork/setup footprint. RSS is the
kernel's per-process high-water measurement, not a summed tree-memory peak or
resident memory at one common instant. A kernel-reported zero is kept as zero,
not treated as proof the process used no memory.

`results.json` includes every observation and, for each fixture/mode, sample
arrays, median, min, max and range for wall, user, system, total CPU and peak RSS.
Counter groups also record instruction samples, range and distinct count. Valid
fixed fixtures must have zero instruction variation. Ratios compare medians to
the native median only when that baseline is positive; otherwise the ratio is
JSON `null`. Ratios are descriptive, not statistical significance or an accepted
regression budget. CI uses one warmup/sample to exercise oracles and serialization;
it imposes no wall-time or overhead-ratio threshold.

## Bounds and failure evidence

Each measured child has a five-second deadline followed by a one-second kill/reap
window. The outer Python controller has an additional bounded fallback and is a
subreaper for unexpected orphaned fixtures. Unexpected descendants invalidate a
sample. Each output stream is capped at 64 KiB; overflow stops the timer and the
controller cleans up any adopted fixture. The child has a 1 GiB address-space
backstop, a CPU-time backstop, a 1 MiB per-file limit and disabled core dumps.
These apply to the fixed fixture experiment and are not production sandbox policy.
The campaign checks a 180-second deadline between invocations; compilation/tool
queries have separate 30-second limits. The summary is capped at 8 MiB.
Each identity input must be a regular file of at most 64 MiB; inventories are
limited to 128 files and 256 MiB in total, with stable file metadata during hashing.

Exit 0 means all scheduled observations, oracles, cleanup and identity checks
passed. Exit 1 means a diagnostic failure; earlier rows remain in `results.json`
but aggregate statistics are withheld. Exit 2 is argument validation failure.
Unavailable or malformed measurements are never silently dropped from a passing
matrix. Bounds are failure backstops, not performance acceptance thresholds.

## Identity and comparison limits

The result records before/after hashes for the client, native collector, `drrun`,
release runtime/extension libraries, runtime build manifest, Python executable,
compiler/assembler/linker/compiler frontend, source inputs and built fixtures/timer.
Compiler-reported bare subtool names are resolved with the same restricted PATH
used for compilation, so a different parent PATH cannot mislabel their identities.
Git HEAD, CPU vendor/model/microcode/ISA flags/topology, kernel, selected affinity,
available frequency-policy information, compiler and Python versions are also
recorded. Changed or missing input identities invalidate the whole experiment and
remove aggregate statistics. The source-dirty flag is descriptive; hashes identify
the inspected files even when a working tree has local changes.

These hashes are provenance clues, not signed build attestations. A native binary
hash does not establish which source built it. The host's complete dynamic-loader
and library environment is not attested. Child environment is restricted to a
fixed PATH and C locale, but this is still a shared host with caches, scheduling,
thermal effects, frequency changes and potentially a busy sibling CPU. Pinning a
logical CPU does not reserve that physical core. Inspect raw samples and identities
before comparing runs; reproduce relevant cases under representative conditions.
JIT, interpreter, syscall-heavy and process-tree overhead need separate fixtures.

Focused policy, resource-accounting and cleanup tests require no SDK:

```sh
python3 -m unittest discover -s tests -p 'test_overhead.py' -v
```
