# A non-flat latency control on the three original graph programs

Forcing Durante's existing non-flat accounting path with the cache switched
off reproduced 99.24% of the additional CPU seen when enabling L1D for the
size-2,048 block queue. For the size-8 block queue that control was slower
than the actual L1D mode on every observed pair. These results direct
optimization toward block charging and store checkpoints under L1D.
A combined inline implementation would need separate cost and counter
validation.

| Original ELF | Latency (ms) | Non-flat callback (ms) | L1D (ms) | Non-flat step / total increase | L1D minus callback, all pairs (ms) |
| --- | ---: | ---: | ---: | ---: | ---: |
| Priority-queue reference | 80.00 | 743.18 | 869.51 | 84.00% | +97.32 to +150.20 |
| Block queue, size 8 | 415.03 | 4,407.22 | 4,123.01 | 107.66% | −318.51 to −258.27 |
| Block queue, size 2,048 | 278.96 | 8,054.38 | 8,113.92 | 99.24% | −52.02 to +224.80 |

The pooled median difference between L1D and the callback control was
+126.33 ms for the reference, −284.21 ms for size 8 and +59.54 ms for size
2,048. Cross-boot timings of the last two modes overlap for size 2,048.
On each boot individually the non-flat control reproduced over 99% of the
increase for that ELF. Across all 90 runs the original output matched,
and all instruction, REP and latency counters were identical for each ELF.
Both pinned CPUs recorded zero steal ticks.

## What the control changes

The [previous five-mode study](../graph_stage_cost/) found that enabling the
L1D mode accounted for 84–96% of the full diagnostic's extra CPU over native.
That transition changes both memory instrumentation and how Durante charges
block work. This experiment adds a control between the original two modes.

| Mode | Measured executable and options |
| --- | --- |
| Latency | Original full diagnostic library with instruction and REP counting and the latency bound |
| Callback | Laboratory build with the exact same arguments and `flat_candidate` fixed to false |
| L1D | Original diagnostic library with the same latency options plus the 32 KiB L1D probe |

The new CMake target defines `DURANTE_TEST_LATENCY_CALLBACK` only in its
laboratory library. The control uses the existing `count_block_latency`
entry callback. Fixing the flat selection also changes the normal
store checkpoint path from inline code to the `before_write` callback.
Enabling L1D in the original library can combine some store checkpoints
with cache accounting. The two differences quantify these complete modes.
Isolating individual clean calls or address computations requires a control
that keeps the rest of the path fixed. [Block-entry dispatch](https://github.com/seo-rii/durante/blob/da50cbe17d145f4ab19ff9c065bde59c3f4235f0/client/durante.c#L2134-L2150)
· [Write checkpoints and probe hooks](https://github.com/seo-rii/durante/blob/da50cbe17d145f4ab19ff9c065bde59c3f4235f0/client/durante.c#L2175-L2204)

## Measurements and reproduction

Each new SMT-disabled AMD Milan guest pinned the same original three
C++20 graph ELFs to CPU 1 and ran five trials per mode, for 90 total runs.
Each three-round block covered all three modes, rotating their positions.
The repeat guest reversed the primary guest's entire sequence. The runner
installed and SHA-256 checked the selected shared object at the same path
outside the timed child. The frozen input, expected stdout, CPUID profile,
launcher and runtime are the same as in the [registered stage payload](../graph_stage_cost/registration.json).
The table gives medians of completed user plus system CPU. The difference
range covers every pair of observed timings from neighboring modes. The percentages divide
the differences in pooled medians by the L1D minus original latency median
difference for each submission. [Per-boot and pooled results](results.json)

The [registration](registration.json) at
[da50cbe](https://github.com/seo-rii/durante/commit/da50cbe17d145f4ab19ff9c065bde59c3f4235f0)
predates creation of both VMs. Reproduce that revision with GCC 12.2.0,
CMake Release, the DynamoRIO 11.3.0-1 SDK and the previous unpacked graph
stage payload. The original client built alongside the control reproduced
the archived ordinary probe byte for byte. An independent second build of
the control reproduced its binary and the full registered archive.

From this directory with Python 3.11 and NumPy:

```sh
cmake -S . -B "$BUILD" -DCMAKE_BUILD_TYPE=Release -DDynamoRIO_DIR="$SDK/cmake"
cmake --build "$BUILD" --target durante_latency_callback
python3 -B prepare.py --stage-payload "$STAGE_PAYLOAD" --client "$BUILD/libdurante_latency_callback.so" --output "$NEW_PAYLOAD"
python3 -B replay.py --verify results.json
python3 -B -m unittest discover
```

The replay checks the installed client hashes, CPU1 kernel exec traces, original
output, terminal counters and their enabled diagnostics. The latency and callback
arms emit only terminal work records. The L1D diagnostic is required only for
the arm that actually measured it. [Guest identities and VM/disk deletion](evidence/cloud.json)
