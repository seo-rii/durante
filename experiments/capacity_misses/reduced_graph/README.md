# Cost of measuring five ML4 features on the 65 ms graph panel

A client that retains five ML4 counters matched the full client on every retained
integer and skipped-operand count in all seven programs. The smaller diagnostic
used 52–268 times native CPU. For the block queue with 2,048 entries per block,
it took 8.13 seconds to measure a program that took 30.30 ms natively.

| Original C++20 ELF | Native (ms) | Full (ms) | Five features (ms) | Full / five | All observed full / five ratios | Five / native |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Priority-queue reference | 14.50 | 964.30 | 914.82 | 1.054 | 1.023–1.078 | 63.1 |
| Fast bucket queue | 10.95 | 850.75 | 781.63 | 1.088 | 1.021–1.174 | 71.4 |
| Block queue, size 8 | 50.22 | 4,273.15 | 4,187.75 | 1.020 | 0.995–1.032 | 83.4 |
| Block queue, size 2,048 | 30.30 | 8,200.57 | 8,134.06 | 1.008 | 0.990–1.013 | 268.5 |
| Block queue, size 4 | 90.01 | 7,609.86 | 7,547.12 | 1.008 | 1.001–1.012 | 83.8 |
| Linear scan | 721.54 | 37,532.80 | 37,591.05 | 0.998 | 0.996–1.026 | 52.1 |
| Ordered set | 22.75 | 1,268.45 | 1,202.86 | 1.055 | 1.038–1.073 | 52.9 |

Times are pooled medians of child user plus system CPU. Each row contains ten
native, four full and four reduced observations across two new GCE Milan boots.
Ratios divide the medians. The range uses all sixteen pairs of measured full and
reduced times. The full client maintains 256 KiB, 2 MiB and 32 MiB cache
capacities. The reduced one maintains the 2 MiB capacity and the same L1D
ordering. The allocated outer-cache arrays shrink from 12.5 MiB to 0.75 MiB.
Both clients still instrument memory operands and process L1D misses in order.

## Simulated verdict accuracy

Each model keeps its original Dijkstra coefficients. The five-feature model
uses the existing weights for latency bound, REP bytes, dependent L1D misses,
dependent 2 MiB misses and stream 2 MiB misses. For each model we multiplied
each submission's score by the native time of the independent reference and
divided by that reference's score. No evaluated submission contributed to
the scale. The table allows all pairings of native reference and instrumented
observations on the two boots. The deadline comparison uses unrounded values.

| Original ELF | Observed native (ms) | Full estimate (ms) | Five-feature estimate (ms) |
| --- | ---: | ---: | ---: |
| Priority-queue reference | 14.44–16.59 | 14.44–16.59 | 14.44–16.59 |
| Fast bucket queue | 10.87–11.91 | 12.56–14.43 | 12.48–14.34 |
| Block queue, size 8 | 49.40–52.65 | 56.85–65.34 | 58.86–67.64 |
| Block queue, size 2,048 | 29.83–33.17 | 47.02–54.04 | 48.57–55.82 |
| Block queue, size 4 | 87.37–90.95 | 101.05–116.13 | 105.11–120.80 |
| Linear scan | 720.24–722.83 | 718.03–825.23 | 748.22–859.92 |
| Ordered set | 21.89–26.24 | 17.57–20.19 | 17.62–20.25 |

Both models agreed with the native 65 ms verdict for five of six evaluated
ELFs on the primary boot and all six on the repeat boot. Pooling preserved
five agreements and one indeterminate prediction. The size-8 block queue
completed below 53 ms in every native run. On the primary boot, the slowest
reference took 16.590 ms and pushed both models' upper estimates above 65 ms.
All five retained counters for each ELF were identical across all eight trials.
The two models differ in the features they score. Even at the same verdict, the
smallest size-2,048 estimate from the reduced model exceeded its largest native
observation by 46%. The seven known submissions share one input. These
observations cover simulated deadlines after completion. [Per-boot results,
CPU ranges and percentage errors](results.json)

## Replay and reproduction

The [registration](registration.json) was published at
[20f0323](https://github.com/seo-rii/durante/commit/20f032336810d30a49da2f9a24c4c9a35742145c)
before either VM was created. The original ELF files, stdin, expected output
and full diagnostic are from the [preceding graph study](../aonohako_shortest_paths/).
The reduced client was built with GCC 12.2.0, CMake Release and the same
DynamoRIO 11.3 SDK. Both diagnostic arms used identical paths and arguments.
Every run returned the expected output for `985955\n`. Each guest used
CPU 1, one thread per core, an x86-64-v2 instrumented profile and the native
host profile for ordinary executions. The second boot swapped the diagnostic
order and reversed program order. The kernel traces identify the measured
execs on CPU 1 for all 126 runs. Both pinned cores recorded zero steal ticks.
[Guest identities and deletion of both VMs and disks](evidence/cloud.json)

From this directory with Python 3.11 and NumPy:

```sh
python3 -B -m unittest discover
python3 -B replay.py --verify results.json
```

To rebuild the guest archive, build `durante_ml4_five` using the
[probe build instructions](../../l1d_probe/README.md), then provide its shared
library and the original unpacked graph payload:

```sh
python3 -B prepare.py --graph-payload "$GRAPH_PAYLOAD" --client "$BUILD/libdurante_ml4_five.so" --output "$NEW_PAYLOAD"
```

A second clean build with the same compiler and SDK reproduced the registered
shared library and archive bytes. The replay authenticates each archived
measurement, checks the retained values against its raw process record and
diagnostic output, and reconstructs every cost and prediction.
