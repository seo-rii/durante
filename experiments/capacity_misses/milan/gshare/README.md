# Transferring the gshare branch counter to the Milan contest panel

Simulated branch misses explain part of the difference between two BFS implementations. They do not explain why set-based Dijkstra exceeds twice the time of the heap implementation on the primary Milan boot.

The set ran 77,900,118 conditional branches with 5,237,116 simulated misses. The heap ran 43,413,274 branches with 3,518,877 misses. The ratio is **1.794385× for branch count and 1.488292× for misses**. The native median ratio was 2.296441× on the first boot and 2.512403× on the second. Every cross-pair on the first boot was above 2× (2.101229–2.449423×). The second boot spans 1.943632–3.287883×, so its 2× verdict is indeterminate. [Results](results.json)

For the same set/heap pair, the instruction, load and REP-byte ratios are 1.376941×, 1.508349× and 1×. The ratio of a nonnegative weighted sum is a weighted average of its component ratios when the anchor score is positive. Even fitting new nonnegative weights on all five counters could yield at most **1.794385×**. A post-hoc check gives the same bound for a nonnegative additive combination of the frozen ML4 score (1.523077×), conditional branches and misses. This bound applies to these additive features and this pair of binaries.

## Transfer results

Both fits came from the pre-existing integer, REP copy, sort, search and rolling-window training archive. The baseline and gshare model have their own fitted instruction and REP weights. The miss coefficient is 6.926585 ns per simulated miss in the gshare fit. Neither model used the nine Milan programs for fitting. Each row below is relative to the intended implementation of its own problem. [Original fitting procedure](../../../../docs/CPP20_GSHARE_MODEL.md) · [Collection protocol](protocol.json)

| Variant | First native median | Second native median | Baseline | With gshare |
| --- | ---: | ---: | ---: | ---: |
| BFS, queue of coordinate pairs | 1.121× | 1.145× | 1.258× | 1.157× |
| BFS, list queue | 1.737× | 1.771× | 2.875× | 1.747× |
| Dijkstra, vector adjacency and getchar | 1.566× | 1.838× | 1.299× | 1.249× |
| Dijkstra, set decrease-key | 2.296× | 2.512× | 1.377× | 1.422× |
| Fenwick, bottom-up segment tree | 1.194× | 1.400× | 0.812× | 1.195× |
| Fenwick, range BIT template | 2.093× | 2.222× | 1.218× | 1.397× |

At the pre-existing 1.5×, 2× and 3× limits, baseline agreement was 12/17 determinate decisions on the first boot and 9/12 on the second. The gshare model reached 13/17 and 10/12. It fixed the BFS list-queue false timeout at 2×. False accepts remained at four on the first boot and two on the second. Pooling the full ranges across boots gave ten agreements and two false accepts among twelve determinate settings, with six indeterminate. These are retrospective, simulated verdicts. The test does not qualify a production clock.

## Evidence and reproduction

The controls and both original fits were recorded in commit `d7bc09d` before local collection. The ELF, input and reference output identities are from the earlier nine-program Milan study. On a single pinned local CPU, the existing `time_features` gshare client ran beside that study's x86-64-v2 meter. All five counters agreed across two separate processes per program, with forward and reverse case order. The original and local instrumented instruction totals differ by at most 0.25 ppm. REP-byte counts agree exactly. The software predictor describes the dynamic branch stream under its own 14-bit history and table. The hardware predictor was not measured. Native time comes exclusively from the 126 native executions on the original two GCE boots. [Milan study](../README.md) · [18 local records](records.json)

From the repository root, replay the published evidence and its regressions:

```sh
python3 -B experiments/capacity_misses/milan/gshare/replay.py > /tmp/gshare-milan.json
cmp /tmp/gshare-milan.json experiments/capacity_misses/milan/gshare/results.json
python3 -B -m unittest discover -s experiments/capacity_misses/milan/gshare -p 'test_*.py'
```

To recollect, use the original unmodified Milan payload, its registered ELF files and inputs, and a fresh output directory. Build the feature client at the study revision with the DynamoRIO 11.3 SDK:

```sh
cmake -S experiments/time_features -B "$BUILD" -DCMAKE_BUILD_TYPE=Release -DDynamoRIO_DIR="$SDK/cmake"
cmake --build "$BUILD" --target time_features_client
python3 -B experiments/capacity_misses/milan/gshare/collect.py \
  --payload "$PAYLOAD" --probe "$BUILD/libtime_features_client.so" --cpu "$CPU" --output "$OUTPUT"
```

The records contain each program's branch counters, same-process meter events and artifact identities. Replay independently verifies the local receipts, original model fit, GCE raw archives and boundary labels before producing the comparison.
