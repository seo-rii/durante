# Allocation traffic and relative contest time

Counting calls to C++ `operator new(size_t)` explains a structural difference between the original Milan Dijkstra executables. A single extra cost per call cannot correct the original 2× set-Dijkstra false accept while keeping the correct 2× BFS list-queue acceptance. Counts were identical on forward and reverse local executions of all 33 registered executable/input pairs. Native timings and cache-model observations come from the earlier CPU-pinned GCE guests. [Local raw receipts](measurements.json.gz) · [Original Milan panel](../milan/)

| Original program | Calls to operator new | Peak outstanding allocation entries |
|---|---:|---:|
| Dijkstra, intended CSR and binary heap | 26 | 17 |
| Dijkstra, vector adjacency and binary heap | 503,071 | 189,960 |
| Dijkstra, vector adjacency and ordered set | 726,828 | 249,959 |
| BFS, intended | 2,493 | 2,015 |
| BFS, list queue | 2,756,474 | 7,661 |

The ordered set requested 223,776 blocks of 48 bytes, while the BFS list queue requested 2,749,979 blocks of 24 bytes. The peak adds entries to libc malloc and calloc and subtracts entries to free with a non-null argument. All 66 runs finished without a negative balance and made zero realloc calls. This entry-based proxy measures concurrent allocation requests. It observes neither the returned pointers nor their memory residency.

## What a shared coefficient can recover

With the original frozen ML4 weights, add the same nonnegative cost to each allocation call on both the reference and the submission. Moving set-Dijkstra past 2× on the first Milan boot requires **more than 63.9902 ns/call**; preserving the BFS list queue's acceptance requires **at most 16.4131 ns/call**. For example, 50 ns predicts 1.896× for set-Dijkstra and 3.001× for BFS. All observed native pairs on that boot gave the opposite verdicts at 2×. The exact upper and lower bounds use each problem's own ML4 coefficients and anchor. [Exact thresholds](results.json) · [Prior skipped-operand comparison](../milan/set_gap/)

We also tested adding one score term proportional to the peak number of outstanding entries. Starting with ML4 plus the frozen 6.92658480142496 ns per simulated branch miss, the set-Dijkstra 2× equality occurs at 235.9861409 ns per peak entry. **236 ns** is the smallest nonnegative whole-number coefficient giving the desired strict timeout. It was chosen on this known case. The transfer applies this same coefficient to all programs, evaluates the five relative limits 1, 1.25, 1.5, 2 and 3, and retains native ranges crossing a threshold as indeterminate.

| Workload | Primary, base → charged correct / native-decisive | Repeat, base → charged correct / native-decisive |
|---|---:|---:|
| Original Dijkstra, used to choose the coefficient | 8 → 9 / 9 | 6 → 6 / 6 |
| Predecessor queries | 18 → 18 / 20 | 18 → 18 / 20 |
| Inversion counts | 20 → 20 / 20 | 20 → 20 / 20 |
| Range distinct queries | 19 → 19 / 20 | 19 → 19 / 20 |
| Layered Dijkstra | 19 → 19 / 19 | 20 → 20 / 20 |

The predecessor multisets peak at 65,549 entries on both inputs. The charge fixes their two false accepts at 1.5× and introduces two false timeouts at 2×. On the layered graph the set peaks at 6,201 entries for both weight schedules even as its `operator new` count grows from 6,513 to 147,825. These figures bound the transfer of a retrospective coefficient; they do not measure the CPU cost of an allocator call. Across-program timing accuracy still depends on the use of the allocated memory. [All case and deadline outcomes](results.json) · [Layered graph](../dijkstra_relaxation/) · [Range queries](../range_distinct/) · [Order statistics](../order_statistics_transfer/)

## Reproduce

The collector uses the four existing registered Linux x86-64 payloads, exact original static executable and input hashes, a DynamoRIO 11.3.0-1 SDK, GCC 12.2.0 and GNU binutils. The 66 local runs used CPU 6 on an AMD EPYC 7B12 with the host CPUID. These runs supplied entry counts only. They do not enter the native time denominators. The experiment counts allocator function entries in these single-threaded, symbol-identified executables. The nine stripped ELFs have their addresses recovered by requesting a linker map from exactly the original build and verifying the resulting ELF SHA-256. See each study's preparation instructions to supply its registered payload. [Original graph](../milan/) · [Order statistics](../order_statistics_transfer/) · [Range queries](../range_distinct/) · [Layered graph](../dijkstra_relaxation/)

From this directory, use an executable local temporary directory for the staged binaries and any available CPU number in place of 6.

```sh
cmake -S . -B "$BUILD" -DCMAKE_BUILD_TYPE=Release -DDynamoRIO_DIR="$SDK/cmake"
cmake --build "$BUILD"
python3 -B collect.py --milan "$MILAN" --order-statistics-transfer "$ORDERS" \
  --range-distinct "$RANGE" --dijkstra-relaxation "$LAYERED" --runtime "$MILAN/dynamorio" \
  --client "$BUILD/liballocator_entries.so" --cpu 6 --output measurements.json.gz
python3 -B replay.py --verify results.json
python3 -B -m unittest discover
```

To verify the published evidence only, run the final two commands with Python 3.11 and NumPy. The replay checks the complete local schedule, input and output hashes, original allocation records and repeated observations, then reuses the registered GCE archive and model readers to recover native CPU times and unrounded clock scores. No new GCE guests or changes to the grading runtime were needed.
