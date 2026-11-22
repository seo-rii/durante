# Measuring the address range of a C++20 dependent load

The sampler distinguished all five active address ranges. Interpolating CPU time from the two endpoint spans passed **0 of 3** new input sizes. The sampler added **1.29%** CPU in diagnostic mode and **13.94%** with the release v11 live work clock. The live cost exceeded the preregistered limit on every input. This evidence supports keeping the feature in the research client. [Scores and paired CPU totals](../experiments/results/gce-cpp20-locality/evaluation.json)

## Held-out predictions on AMD Milan

| Active range | Sampled span (bytes) | Native CPU median (min–max), ms | Span estimate, ms | Error |
| --- | ---: | ---: | ---: | ---: |
| 32 KiB, calibration | 29,908 | 18.79 (18.47–19.28) | — | — |
| 128 KiB | 128,212 | 22.86 (22.59–23.15) | 53.27 | +133.0% |
| 2 MiB | 2,094,292 | 44.48 (42.60–47.47) | 119.44 | +168.5% |
| 16 MiB | 16,774,356 | 91.19 (67.79–144.38) | 168.72 | +85.0% |
| 64 MiB, calibration | 67,104,768 | 201.57 (182.71–231.07) | — | — |

All 90 sampled hot-site reports agreed with the independent walk oracle on 2,097,573 loads, 2,049 far pairs and zero near pairs. All 60 span-enabled reports also agreed on the exact spans above, covering 91.27% to 99.99% of the active ranges. The latency-only baseline predicted about 20.37 ms for each fresh input and passed only at 128 KiB. The reference native spread exceeded 10% at 2, 16 and 64 MiB, and the 32 KiB native minimum fell below 20 ms. The copy cost control also exceeded the native spread threshold. All observations are retained. [Per-run data](../experiments/results/gce-cpp20-locality/dataset.json.gz)

As a sensitivity check after scoring, take the fastest measured run at both calibration endpoints and compare the resulting interpolation to the slowest held-out native run. At 128 KiB the prediction is still 49.45 ms against 23.15 ms (+113.6%). At 2 MiB it is 108.91 ms against 47.47 ms (+129.4%). At 16 MiB the resulting 153.20 ms is about 6.1% above the slowest native observation. The observed variation leaves this third comparison less conclusive. On the first two inputs the timing model fails even with these favorable choices.

## Extra CPU with the live work clock

| Comparison over seven inputs | Baseline CPU, s | With sampler change, s | Change | Largest per-input increase |
| --- | ---: | ---: | ---: | ---: |
| v11 diagnostic → near/far sampler | 39.955 | 40.423 | +1.17% | +4.35% |
| Diagnostic near/far → same sampler plus span | 40.423 | 40.472 | +0.12% | +0.84% |
| v11 diagnostic → span sampler | 39.955 | 40.472 | +1.29% | +4.37% |
| v11 live work clock → span sampler | 7.057 | 8.040 | +13.94% | +18.04% |

The full sampler passed both cost thresholds in diagnostic mode. With the live clock, per-input increases ranged from 11.16% to 18.04%, exceeding the 10% gate on all seven. The diagnostic near/far comparison isolates the extra work to record extrema in that mode. The live comparison includes the entire second client, including loading, per-site countdowns and callbacks. It cannot apportion the observed cost to a particular sampler instruction. [Per-case CPU ratios and all paired ranges](../experiments/results/gce-cpp20-locality/evaluation.json)

All 259 runs had the expected output. Their 469 measured exec events were on CPU 1. The guest recorded zero CPU 1 steal ticks, trace overruns and dropped events. The canonical release reader reproduced all 210 metered reports. All 84 terminal live clocks reconciled to the instruction and REP counters of the corresponding process. Both the temporary VM and its boot disk returned HTTP 404 after deletion, and the temporary SSH key was removed. [Trial receipts](../experiments/results/gce-cpp20-locality/receipts.tar.xz) · [Kernel exec trace](../experiments/results/gce-cpp20-locality/exec-trace.txt) · [Teardown](../experiments/results/gce-cpp20-locality/cleanup.json)

## What was measured

Aonohako's earlier test used one static C++20 executable for two inputs. Its native medians on a pinned Milan CPU were 18.28 and 210.07 ms, although both runs executed the same number of dependent loads and had nearly the same modeled cycles and nominal operand bytes. This follow-up keeps the executables, the two endpoints and the loop count. It adds three previously unmeasured intermediate ranges. Two more executables, a sort and a REP copy, serve as instrumentation cost controls. [Earlier corpus and results](https://github.com/seo-rii/aonohako/pull/154) · [Frozen plan](https://github.com/seo-rii/durante/commit/7662935e83035118da908167425cc0f619c205ae)

All five walks initialize the same 64 MiB table. At file offset `0x1a00`, each of 2,097,573 four-byte loads supplies the index for the next. A power-of-two mask selects the active index range. The original client samples visits 1 and 2, 1025 and 1026, and so on at each eligible instruction in the main executable. It counts a pair as near if the two addresses occupy the same or adjacent 64-byte lines. The experimental client also keeps the lowest and highest sampled starting addresses and emits their difference in bytes per instruction. Updating these extrema reuses the existing callback. The recorded diagnostic contains the instruction's ELF offset and the address difference. [Fixture source](https://github.com/seo-rii/aonohako/blob/b938afaa6c171a8a56e24d376d6662f93fcea4c6/docs/durante/gce-v11-cpp20-accuracy/corpus/sources/cycle_walk.cpp) · [Probe](../experiments/cpp20_locality/probe)

The distinction between range and bytes touched matters most at 16 and 64 MiB. The recurrence has full period on each power-of-two table (its increment is odd and its multiplier equals one modulo four). At both sizes the traversal ends before repeating an index. Each therefore reads exactly 2,097,573 distinct four-byte elements, about 8.002 MiB in total, spread over different address ranges. At 32 KiB the fixed sampling phase sees only 16 distinct elements in 4,098 observations. Hardware cache misses and DRAM traffic were outside the experiment.

The duration target is the median of six direct native child CPU observations, including initialization. The frozen estimator interpolates between the two native endpoint medians in log sampled-address span. Only the endpoints contribute timing labels to the fit. For comparison, a second estimator fits one coefficient to the modeled cycles at the same endpoints. A fresh input passes when its estimate is within ±20% of the native median. The separate cost gate allows up to 5% extra instrumented CPU over the seven inputs together and 10% per input. [Analysis plan](../experiments/cpp20_locality/analysis-plan.json)

Six arm orders balance position and preceding arm for each input. One native warmup per input precedes six scored repetitions. All arms run in a CPU 1 cgroup with single-CPU affinity. A CPU 0 controller collects kernel exec placement. The two live arms use Aonohako's release v11 client and poll its shared work clock every 5 ms under a nonbinding 100-billion-unit budget. The independent trial deadline is 120 seconds. The cost comparison uses `wait4` user and system CPU for the instrumented child, including initialization and sampler startup. The polling process, job queue, HTTP handling and sandbox supervisor fall outside that CPU total. [Collector](../experiments/cpp20_locality/bench.py) · [Exact schedule and tool digests](../experiments/cpp20_locality/protocol.json)

## Reproducing the measurements

Offline verification requires Python 3.11 or later. The verifier replays every archived per-process record through the release v11 reader, compares the final live clock to the release totals, checks each output and matches the exec trace to the CPU placement. The scorer independently checks both sampling schemas, the walk oracle, the held-out timing errors and the paired CPU costs. The original ELF archive is optional for offline replay. To include the executable checks, run the following from the Durante checkout with `aono` pointing to a checkout of Aonohako.

```sh
study=experiments/cpp20_locality
data=experiments/results/gce-cpp20-locality
tmp=$(mktemp -d)
git -C "$aono" show b938afaa6c171a8a56e24d376d6662f93fcea4c6:docs/durante/gce-v11-cpp20-accuracy/corpus/elfs.tar.xz > "$tmp/elfs.tar.xz"
python3 "$study/verify.py" "$data" --elf-archive "$tmp/elfs.tar.xz"
python3 "$study/score.py" "$data/dataset.json.gz" "$data/receipts.tar.xz" --verify "$data/evaluation.json"
python3 -m unittest discover -s tests -p 'test_cpp20_locality*.py'
```

For a new collection use Linux x86-64, GCC 12.2, the patched DynamoRIO 11.3.0-1 SDK and the v11 installation from the pinned Aonohako source. The payload builder checks each executable, DSO, runtime component and frozen input against the registered digest before writing the payload. To build from source at that Aonohako revision, use its [client build](https://github.com/seo-rii/aonohako/blob/b938afaa6c171a8a56e24d376d6662f93fcea4c6/scripts/build_durante_client.sh) and [runtime install](https://github.com/seo-rii/aonohako/blob/b938afaa6c171a8a56e24d376d6662f93fcea4c6/scripts/install_durante_runtime.sh) scripts with `third_party/durante-v11`.

```sh
cmake -S experiments/access_sample -B "$tmp/near" -DCMAKE_BUILD_TYPE=Release -DDynamoRIO_DIR="$DynamoRIO_DIR"
cmake --build "$tmp/near" --target access_sample
cmake -S "$study/probe" -B "$tmp/span" -DCMAKE_BUILD_TYPE=Release -DDynamoRIO_DIR="$DynamoRIO_DIR"
cmake --build "$tmp/span" --target access_span
python3 "$study/probe/check_span.py" --drrun "$release/dynamorio/bin64/drrun" --meter "$release/lib/libdurante.so" --probe "$tmp/span/libaccess_span.so"
python3 "$study/prepare.py" "$tmp/payload" --release "$release" --near "$tmp/near/libaccess_sample.so" --span "$tmp/span/libaccess_span.so" --elf-archive "$tmp/elfs.tar.xz"
```

Copy the prepared directory to a disposable AMD Milan `n2d-standard-4` guest configured with one thread per core, then run `sudo taskset -c 0 ./guest.sh` from that directory. The guest writes the dataset, kernel trace and per-run receipts under `output/`. The [exporter](../experiments/cpp20_locality/export.py) shows how the published archive was built from the captures and the provider's teardown receipts.
