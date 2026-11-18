# Replaying CPP20 branch and sort timing differences

Aonohako's CPP20 reference collection found two pairs in which sorting took
longer than the branch workload even though Durante reported fewer instructions,
a lower static latency bound and the same REP bytes. Both fitted models missed
Aonohako's ±20% target on all nine validation cases. This experiment tests which
additional counters can distinguish the two pairs. The timings below come from
the original native runs. [Aonohako reference results](https://github.com/seo-rii/aonohako/pull/121)

## Measurements

For each scale, the runner takes the median of the six original native CPU
times that bracketed the three metered runs. It then replays the original
executable and stdin three times with ASLR disabled on one logical CPU.
Each replay must produce the original expected stdout digest. One optional
stock DynamoRIO cache simulation per case supplies L1 data and unified
last-level demand misses. The receipt records all repetitions, archive and
tool hashes, and the observed ranges. [Recorded receipt](../experiments/time_features/cpp20-reference-replay.json)

The predictor is the exploratory client's 14-bit gshare simulation. For the
scalar load counter, each qualifying static instruction has its own previous
starting 64-byte line. The first access gets its own count. Later accesses are
classified as same line, adjacent in either direction, or nonadjacent. An access
that crosses a line is classified using its starting address. Eligible x86-64
instructions are near integer MOV, MOVSX, MOVSXD and MOVZX loads into a GPR.
RSP/RBP-addressed loads, implicit reads, read-modify-write instructions, REP,
prefetch and SIMD are excluded. Instrumentation runs before the load and counts
attempts. The supported fixtures are single-threaded static executables with
fixed code. A second application thread aborts the run.

### Independent controls

One static x86-64 ELF reads 32,768 aligned 32-bit words and executes the same
hot loop in four modes. Changing the stride from 1 to 257 permutes the same
words. Reordering the branch outcomes preserves the number of times each arm
executes. A separate Python checksum calculation checks all four native outputs.

| Branch order | Word stride | Gshare misses | First | Same line | Adjacent | Nonadjacent |
|---|---:|---:|---:|---:|---:|---:|
| Predictable | 1 | 43 | 2 | 30,720 | 2,047 | 0 |
| Predictable | 257 | 43 | 2 | 0 | 0 | 32,767 |
| Permuted | 1 | 16,663 | 2 | 30,720 | 2,047 | 0 |
| Permuted | 257 | 16,663 | 2 | 0 | 0 | 32,767 |

The three repeats agreed in every cell. The second first access is the input
digit read from a different instruction. The runner checks the hand-counted
line totals in every mode and repeat, and checks that permuting the branch
outcomes increases gshare misses for each stride.

### Archived comparisons

| Archived case | Native CPU median (ms) | Gshare misses | Load instructions | Nonadjacent loads | L1D misses | Unified LL misses |
|---|---:|---:|---:|---:|---:|---:|
| branch-1 | 34.1025 | 2,449,834 | 16,808,535 | 1,687 | 726 | 1,454 |
| sort-2 | 71.154 | 9,486,185 | 31,881,760 | 438,125 | 575,116 | 34,335 |
| branch-2 | 66.3895 | 4,897,962 | 33,585,751 | 1,687 | 726 | 1,454 |
| sort-4 | 145.396 | 19,557,754 | 67,631,248 | 911,056 | 1,300,671 | 67,440 |

All three feature passes agreed within the recorded run. A separate invocation
from a different directory gave slightly different counts. For example, sort-2
gshare misses ranged from 9,486,169 to 9,486,185 across the two invocations.
The classifications below agreed.

For a branch time B and sort time S, both predictions can fit within ±20%
only if their ratio (sort / branch) reaches at least 0.8 S / (1.2 B). The
required ratios here were approximately 1.391 and 1.460. For each feature,
the runner compares the lowest observed sort count with the highest branch
count using the exact fractions. It also records when the observed ranges
straddle the boundary.

Gshare misses, total load instructions, same-line, adjacent and nonadjacent
loads, and both simulated cache miss counts each spanned the required ratio in
both pairs. The original instruction, latency and REP counts could not span
either ratio, even in a nonnegative combination. Total conditional branches
also fell short. Several candidate counters move together, so these pairs
cannot determine their weights or establish prediction accuracy. Both pairs
use the same two algorithms.

The archived meter enforced x86-64-v2. The stock replay core exposes the host
CPU. Static glibc may select a different instruction path even with identical
ELF, stdin and stdout. Simulated misses depend on the address stream and the
simulator configuration. A lower-cost candidate for the next test is the total load-instruction count,
which the client already computes at basic-block entry. Compare its prediction
error and instrumentation overhead with gshare and per-load locality, using
the judge’s CPUID profile on a dedicated reference machine. Reserve previously
unseen algorithms, including cases that vary branch predictability independently
of locality.

## Reproduce

Use Linux x86-64, Python 3, a C/assembly toolchain, and the pinned stock
DynamoRIO 11.3.0-1 release. Supply an archived Aonohako CPP20 dataset, a
checkout of the fixture sources, and the compiler image from that archive.
The recorded dataset SHA-256 is
`d1fd7c1f03e541a75ff45fef104a9e3af9de16956995c514634ec187ccde11d7`.
The runner checks this digest by default. For a new validated collection, pass
its independently recorded digest with `--dataset-sha256` and use its compiler
image. The original compiler was Debian g++ 14.2.0-19. Its local Docker image
ID appears in the command below. [Fixture sources](https://github.com/seo-rii/aonohako/tree/4e1d45b6cd007b694fe6467d5bba96e816436008/internal/durante/reference/fixtures) · [Aonohako collection instructions](https://github.com/seo-rii/aonohako/blob/fa4b95e744b10e3442f62bcbfa9a772882b63212/docs/durante-reference.md)

Run from the Durante checkout. Set `AONOHAKO` to the Aonohako clone,
`DATASET` to the archived JSON file, `SDK` to the absolute path of
the stock SDK, and `CPU` to an allowed logical CPU.

```sh
RUN=$(mktemp -d)
FIXTURES="$RUN/fixtures"
IMAGE=sha256:5f1fee5fb589c9494f2155478bab0e01af620f73e99ebf2ff9177a09626d5bf3
SOURCE_REV=4e1d45b6cd007b694fe6467d5bba96e816436008
for name in branch sort; do
    mkdir -p "$FIXTURES/$name"
    git -C "$AONOHAKO" show "$SOURCE_REV:internal/durante/reference/fixtures/$name.cpp" \
        > "$FIXTURES/$name/Main.cpp"
    docker run --rm --network none --user "$(id -u):$(id -g)" \
        -e LANG=C.UTF-8 -e LC_ALL=C.UTF-8 -v "$FIXTURES/$name:/work" -w /work \
        --entrypoint /usr/bin/g++ "$IMAGE" Main.cpp -O2 -Wall -lm --static -pipe \
        -DONLINE_JUDGE=1 -std=c++20 -o Main
done
printf '16777216 44257\n' > "$FIXTURES/branch/1.stdin"
printf '33554432 44257\n' > "$FIXTURES/branch/2.stdin"
printf '1048576\n' > "$FIXTURES/sort/2.stdin"
printf '2097152\n' > "$FIXTURES/sort/4.stdin"

cmake -S experiments/time_features -B "$RUN/build" -DCMAKE_BUILD_TYPE=Release \
    -DDynamoRIO_DIR="$SDK/cmake"
cmake --build "$RUN/build" --target time_features_client
cc -nostdlib -static -no-pie -Wl,--build-id=none \
    experiments/time_features/control.S -o "$RUN/control"
python3 experiments/reference_features.py --dataset "$DATASET" --fixtures "$FIXTURES" \
    --sdk "$SDK" --client "$RUN/build/libtime_features_client.so" --control "$RUN/control" \
    --cpu "$CPU" --repeats 3 --timeout 300 --output "$RUN/replay" --cachesim-root "$SDK"
```

Use a full stock SDK extraction for `--cachesim-root`. Omit that option to
collect just the feature-client observations with the smaller SDK. The host
must allow disabling ASLR for a child process. Results, raw client counters
and optional simulator output are written to a fresh output directory. A run
that fails partway keeps its partial receipt with `complete: false`.
