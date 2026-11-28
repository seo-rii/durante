# Full cache observations with flat work accounting

The standalone research client uses Durante's ordinary flat block-entry charge
while observing the complete cache hierarchy. Each write keeps its ordinary
checkpoint, including the callback fallback when its identity cannot be packed.
Removing fusion separates the cache callback from this checkpoint. RMW operands
still contribute two memory accesses, and all existing cache and dependency
classification code runs. A requested flat-cache run fails if raw TLS cannot be
allocated. The standalone CMake target is the only target defining this option.

The comparison uses the same reference, block8 and block2048 graph ELFs, stdin,
stdout oracle, runtime, full original probe and guest harness used in the
previous stage and callback study. We froze the new client's ELF, source hashes
and complete payload before creating either guest. The original and candidate
were installed at the same path with identical options for each trial. Each arm
ran five times on CPU 1 per guest, including a native arm. The second guest
reversed the complete schedule. [Preregistration](https://github.com/seo-rii/durante/commit/685248203bd010664b2f6d343ed475c604255359) · [Protocol](protocol.json)

## Process CPU on two independent Milan boots

| Graph submission | Native | Original full cache | Flat full cache | Median speedup | All-pair speedup |
|---|---:|---:|---:|---:|---:|
| reference | 14.84 ms | 957.4 ms | 524.4 ms | 1.83× | 1.68–2.07× |
| block8 | 49.60 ms | 4244.3 ms | 1011.2 ms | 4.20× | 4.10–4.42× |
| block2048 | 29.86 ms | 8212.8 ms | 1132.3 ms | 7.25× | 6.91–7.37× |

Times include process user and system CPU. The speedup interval pairs every
original and candidate observation, including across boots. [Per-boot samples and CPU summaries](results.json)

Both guests produced identical work and full-cache counters for all three
programs across the two instrumented arms. The complete diagnostic stderr
was byte-identical per program. The start and end receipts matched on every
field after omitting each run’s PID and segment identity. All eight frozen ML4 feature
values on each program matched the original seven-program graph study.
Pooled candidate medians still ranged from 20.4× to 37.9× native. The VMs
and boot disks returned 404 after cleanup and the temporary keys were
removed. [Lifecycle evidence](evidence/cloud.json)

The memory control checks ordinary reads, writes, RMW, a cross-line operand,
reverse and forward REP and live-register preservation. The cold and warm arms
both produced 291 memory references, 297 line accesses and 16 misses. The
dependent-load control produced four misses and two dependent misses at every
outer capacity. After explicit SIGUSR1 handling, both clients resumed with
the same output and recorded 55 instructions, scaled latency 220, one signal,
and the expected incomplete-cache diagnostic. All ten local fixture executions
also compared the complete terminal work records. This signal test covers an
application-requested signal at a syscall boundary. Other asynchronous interruption positions remain untested. [Local control receipts](controls.json)

Under the frozen compiler and SDK, a second build and packaging step reproduced
the candidate and complete registered archive byte for byte. The ordinary
production build before and after this change also matched byte for byte.
These results characterize diagnostic execution overhead on three static
programs and the recorded cache events. Qualifying a judging deadline
requires separate accuracy evidence on new submissions.

## Reproduction

Run the offline replay on Python 3.11 with NumPy from the repository root.

```sh
study=experiments/capacity_misses/flat_cache
python3 -B "$study/replay.py" --verify "$study/results.json"
python3 -B -m unittest discover -s "$study" -p test_replay.py
```

To reconstruct the exact binary use the registration revision above, GCC
12.2.0 and the DynamoRIO 11.3.0-1 SDK. Supply the previous callback-control
guest payload. The fixture controls use this payload's patched drrun.

```sh
cmake -S "$study" -B "$BUILD/flat" -DCMAKE_BUILD_TYPE=Release -DDynamoRIO_DIR="$SDK/cmake"
cmake --build "$BUILD/flat"
python3 -B "$study/prepare.py" --control-payload "$CONTROL_PAYLOAD" \
  --client "$BUILD/flat/libdurante_flat_cache.so" --output "$BUILD/payload"
cmp "$BUILD/payload.registration.json" "$study/registration.json"
cmake -S experiments/l1d_probe -B "$BUILD/fixtures" -DCMAKE_BUILD_TYPE=Release \
  -DDynamoRIO_DIR="$SDK/cmake" -DDURANTE_TEST_DRRUN="$CONTROL_PAYLOAD/dynamorio/bin64/drrun"
cmake --build "$BUILD/fixtures"
python3 -B "$study/verify_controls.py" --drrun "$CONTROL_PAYLOAD/dynamorio/bin64/drrun" \
  --original "$CONTROL_PAYLOAD/original.so" --fast "$BUILD/flat/libdurante_flat_cache.so" \
  --fixtures "$BUILD/fixtures"
```
