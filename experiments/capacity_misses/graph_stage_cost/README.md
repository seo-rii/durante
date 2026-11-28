# Stage costs of the graph diagnostic on pinned Milan

## Where the measured CPU time went

Enabling the 32 KiB L1D simulation accounted for 84–96% of the
additional CPU over native across three original graph programs. This transition changes how both memory accesses and whole blocks are instrumented. The
size-2,048 block queue took 278 ms with the latency bound, 8.074 s with the
L1D probe and 8.190 s with the outer caches. Both boots show the same ordering.

| Original C++20 ELF | Native (ms) | Instruction + REP (ms) | + latency (ms) | + L1D (ms) | + outer (ms) | L1D step / excess CPU |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Priority-queue reference | 14.65 | 67.84 | 79.20 | 860.09 | 948.57 | 83.61% |
| Block queue, size 8 | 49.63 | 256.15 | 415.49 | 4,114.57 | 4,228.29 | 88.52% |
| Block queue, size 2,048 | 29.98 | 178.17 | 278.45 | 8,073.69 | 8,189.67 | 95.53% |

For the size-2,048 queue, the L1D step added 7.773–7.827 seconds across all
observed pairs, compared with 0.070–0.186 seconds for the outer step.
The 32 KiB model counted 1,212,659 misses in 81,019,334 line lookups for
this case, a 98.50% hit rate. Across the three cases, 92.76–98.50% of
simulated L1D line lookups hit. The next comparison should isolate block-entry accounting from memory-operand
instrumentation under the L1D mode. Its other possible costs include address
calculation, register saves, line-hit checks and miss processing. The line-hit
rate alone cannot assign time to any of those paths.

All 150 executions returned the expected output. Across all instrumented
observations for each program, instructions and REP counts matched exactly.
Latency bounds matched in all enabled modes and L1D counts matched with and
without outer caches. Both pinned cores recorded zero CPU steal ticks.

## What changes at each step

All four instrumented modes use the same registered client, patched launcher,
static submission ELF, stdin, working directory and virtual x86-64-v2 profile.
The baseline runs each executable directly with the host CPU profile.

| Mode | Complete measured configuration |
| --- | --- |
| Native | Direct process |
| REP | DynamoRIO with the ordinary Durante instruction and MOVS/STOS counters |
| Latency | REP plus the static per-block latency bound |
| L1D | Latency plus the 32 KiB, eight-way cache simulation |
| Outer | L1D plus the 256 KiB, 2 MiB and 32 MiB capacities, stream classification and dependent-load counters |

The difference between consecutive medians is the cost difference between
these complete modes. In the registered diagnostic build, the flat
block-entry path requires both memory-operand byte counting and the cache
probe to be disabled. For these static ELFs the latency-only mode can use
`insert_flat_latency_charge` when flat inlining is available. Enabling the L1D
probe disables that path and the latency-enabled block dispatcher can insert
a clean call to `count_block_latency`. At the same time the client emits
instrumentation for application memory operands. The 84–96% step contains
both changes. [Flat-path selection](https://github.com/seo-rii/durante/blob/eb28afa52558cd2772cf79a88e2136b812a94421/client/durante.c#L2659-L2665)
· [Block-entry dispatch](https://github.com/seo-rii/durante/blob/eb28afa52558cd2772cf79a88e2136b812a94421/client/durante.c#L2141-L2150)

Native to REP includes DynamoRIO, the CPU profile, process initialization
and reporting. Each mode runs to completion. The timings describe full
runs of the original 65 ms panel.

The pool has ten readings per mode and submission. Each stage's range pairs
every reading of one mode with every reading of its neighbor. Percentage
shares divide the difference between consecutive medians by the difference
between full and native medians for the same submission. The raw times
include Linux child user and system CPU. [Per-boot and pooled ranges](results.json)

## Inputs and verification

The probe, runtime, three C++20 executables and input match the [original
shortest-paths study](../aonohako_shortest_paths/). All were fixed before
launch in the [registration](registration.json) at
[1631817](https://github.com/seo-rii/durante/commit/163181754647420beb9f603e91ad30d1d21175fd).
Each new n2d-standard-4 AMD Milan guest used CPU 1 with SMT disabled. Each
three-case round used one mode. Every block of five rounds covered all modes,
and their starting positions rotated across the five blocks. The second VM
reversed all 75 trials. Every trial had to reproduce the registered output
for `985955\n` and return exit 0. The exec traces bind measured processes to
CPU 1. [Guest identifiers and deletion of both VMs and disks](evidence/cloud.json)

From this directory using Python 3.11 and NumPy:

```sh
python3 -B replay.py --verify results.json
python3 -B -m unittest discover
```

To rebuild the guest archive, supply the original unpacked graph payload
from the preceding study. The command emits a sibling archive and registration
manifest. The recorded inputs reproduce the exact archive SHA-256.

```sh
python3 -B prepare.py --graph-payload "$GRAPH_PAYLOAD" --output "$NEW_PAYLOAD"
```
