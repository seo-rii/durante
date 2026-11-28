# Corrected cache counters on two Milan guests

Nine C++20 programs ran on CPU 1 of two SMT-off AMD Milan Compute Engine guests.
Each program produced the expected output in seven native and two diagnostic runs
per boot. All nine had identical recorded counters across all four diagnostic
runs, including the corrected stream and dependent-load counts. The observed CPU
steal on the pinned core was zero ticks in both collections. [Protocol](protocol.json) · [Guest and teardown records](evidence/cloud.json)

The panel includes the intended BFS, Dijkstra and Fenwick implementations
and two existing contest variants per problem. Four models used coefficients
from the earlier Xeon dataset. For each problem its intended solution and its
variants were absent from the 35 training cases. Each simulated deadline was
1.5, 2 or 3 times the intended solution under the same clock. A native or
predicted label is determinate only when every observed submission/anchor
pair gives the same verdict. Equality passes. The registration and the exact
weights preceded both GCE boots. [Frozen source revision](https://github.com/seo-rii/durante/commit/eb28afa52558cd2772cf79a88e2136b812a94421) · [Weights and identities](frozen.json)

| Model | Primary disagreements / determinate | Repeat disagreements / determinate | Pooled false accepts | Pooled false timeouts | Pooled determinate |
|---|---:|---:|---:|---:|---:|
| M0, instruction and REP counts | 5/17 | 3/12 | 2 | 1 | 12/18 |
| M2, M0 and capacity misses | 5/17 | 3/12 | 2 | 1 | 12/18 |
| M4, M2 and dependent misses | 2/17 | 1/12 | 1 | 0 | 12/18 |
| ML4, latency bound, REP and memory features | 1/17 | 0/12 | 0 | 0 | 12/18 |

Pooling compares the full ranges across both guests. Six comparisons have
overlapping native ranges. One of them, set-based Dijkstra at 2×, was a clear
ML4 false accept on the primary guest. Its repeat-run native range crossed
the limit. The median probe/native CPU ratio over the nine programs was
52.66× on the primary and 46.79× on the repeat, with individual ratios from
34.88× to 84.40×. [All verdicts and per-program costs](results.json)

These are simulated decisions from completed diagnostic runs on three known
problems. The raw data contain 126 native and 36 diagnostic executions. All
nine rebuilt ELF hashes differ from the Xeon archive, while the sources, full
input bytes and expected outputs match. Native code used the host CPU profile
and the new diagnostic used x86-64-v2. The original Xeon study used its host
profile for the diagnostic. Hardware, executable and probe changes all enter
this comparison. A separate held-out live-deadline study is needed to qualify
a judging clock.

## Reproduction

From the repository root with Python 3.11 and NumPy, run

```sh
study=experiments/capacity_misses/milan
python3 -B "$study/replay.py" --verify "$study/results.json"
python3 -B -m unittest discover -s "$study"
```

The replay re-fits the registered weights from the 35 original samples per
problem, verifies the two raw archives, reconciles every child PID and measured
CPU with the scheduler trace, rebuilds the counters from terminal records and
diagnostic stderr, then classifies every decision. During diagnostic launch
the kernel records drrun executing the DynamoRIO loader. The loader maps the
application internally. The guest runner checks the registered ELF identity
before scheduling and verifies stdout on every run.

To prepare a new payload, build the merged research probe with GCC 12.2.0,
CMake Release and the 11.3.0-1 SDK, then supply the extracted x86-64 runtime
used with the v13 worker. The collector uses Linux x86-64, `taskset`,
`setarch`, tracefs and systemd CPU affinity. With the study scripts operating
on their generated files, `registration.json` identifies every staged payload
member, including the runtime libraries, launcher and client. Each entry
names a file and its SHA-256 separately. The replay also reconstructs and
checks the exact bytes of the original preregistration used by both guests.

```sh
cmake -S experiments/l1d_probe -B /tmp/durante-milan-build -DCMAKE_BUILD_TYPE=Release -DDynamoRIO_DIR="$SDK/cmake"
cmake --build /tmp/durante-milan-build
python3 -B "$study/prepare.py" --runtime "$V13_RUNTIME/dynamorio" --client /tmp/durante-milan-build/libdurante_l1d.so --output "$NEW_DIR"
```
