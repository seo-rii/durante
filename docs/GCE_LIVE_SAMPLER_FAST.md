# Reducing the CPU cost of sampled load addresses

On ten inputs from three previously profiled C++20 executables, restricting instrumentation to selected instructions saved **10.28%** of child CPU against the full sampler. The extra CPU over the ordinary live clock was **0.20%**. On the three inputs held out from site selection those figures were a **10.71% saving** and **1.28% overhead**. Every profiled input passed its CPU and coverage gates. [Scored results](../experiments/results/gce-live-sampler-fast/evaluation.json)

## Results on AMD Milan

| Sampling mode | Child CPU over 60 runs | Relative to Live | Relative to Full | Scored data retained |
| --- | ---: | ---: | ---: | --- |
| Live | 10.978504 s | — | — | v11 work counters |
| Full, JSON | 12.260257 s | +11.68% | — | All eligible sites |
| Packed, binary | 12.074255 s | +9.98% | −1.52% | All eligible sites |
| Profiled, JSON | 11.000303 s | +0.20% | −10.28% | Selected sites, 96.93% of eligible visits |

The profiled result covers 440,230,302 of 454,196,592 eligible visits, with **95.24% to 99.64%** coverage across individual runs. The largest per-input overhead against Live was 4.35% on the original sort. The 16 MiB walk happened to use 7.33% less CPU in Profiled than in Live. Dropping that input as a sensitivity check gives +1.28% against Live and a 10.44% saving against Full over the other nine. Keep all ten for the registered decisions. [Trial dataset](../experiments/results/gce-live-sampler-fast/dataset.json.gz)

| Held-out input | Eligible visits captured | Profiled CPU vs Live | Profiled CPU vs Full |
| --- | ---: | ---: | ---: |
| Pointer walk, 256 KiB | 99.08% | +0.80% | −13.77% |
| Sort, 1,048,576 elements | 95.40% | +3.94% | −7.59% |
| REP copy, 1,024 iterations | 99.61% | −1.13% | −12.16% |

The catalog, countdown and full-report arms used 2.35% more CPU than Live, 6.44% more than Catalog and 2.50% more than Gate, respectively. The control that only discovers eligible instructions therefore accounts for part of the full overhead. Adding countdown code, callbacks and per-site reporting accounts for a further increase. Packed reduced total uncompressed sampler output from 4,138,326 to 2,019,552 bytes (51.20%). Its 1.52% CPU saving missed the 3% target. Profiled reduced output to 27,354 bytes and avoided instrumentation for the other sites. Its observed saving includes both changes. Checking the ELF in the parent immediately before each profiled launch may also affect cache state. These are costs for the measured child configurations. [Original sidecars and process records](../experiments/results/gce-live-sampler-fast/receipts.tar.xz)

The 60 Gate and 60 Packed reports matched Full on the entire ordered set of instruction offsets, visit counts and pair counts. All 60 Profiled reports matched Full restricted to the selected offsets, including near/far counts and spans. For separate Full and Packed runs, 48 non-oracle site comparisons differed in span and all near/far counts agreed. Tests on deterministic assembly compare the full decoded reports exactly and exercise packet sizes on both sides of the 32-row write boundary. Across the 60 pairs, the full v11 work vector matched in 56 Packed and 57 Profiled runs. The remaining instruction differences were at most 16, and both REP byte totals always agreed. Every one of the 360 terminal clocks reconciled to its own canonical v11 records. [Machine-code controls](../experiments/live_sampler_fast/probe/check_controls.py)

The guest executed 370 trials and recorded 730 measured exec events on CPU 1. CPU 1 steal and trace losses were zero. Both the temporary instance and its boot disk returned HTTP 404 after deletion, and the temporary key was removed. [Kernel trace](../experiments/results/gce-live-sampler-fast/exec-trace.txt) · [Teardown receipt](../experiments/results/gce-live-sampler-fast/cleanup.json)

## Two ways to collect a span

The earlier experiment traced 84.71% of the extra instrumented CPU to the change from loading an empty client to finding eligible instructions, inserting their counters, executing the countdowns and writing per-site visits. We tested two candidates. **Packed** keeps every eligible site, the original sampling cadence and all six per-site fields, and emits fixed-width records in chunks of up to 32 sites. **Profiled** keeps the original JSON and instruments only the registered offsets for the exact executable. A new translation-only **Catalog** control enumerates eligible translated instructions without adding per-visit code. The other arms are the ordinary v11 **Live** clock, the existing **Gate** countdown control and the original **Full** address sampler. [Previous cost decomposition](GCE_LIVE_SAMPLER_COST.md) · [Candidate code](../experiments/live_sampler_fast/probe)

For each executable, we ranked load sites by dynamic visits in the original study's 42 live-span runs, using ascending file offset to break ties. We selected the smallest prefix accounting for at least 95% of those visits, up to a maximum of 64 sites. The resulting profile contains one offset for the pointer walk (99.08% of training visits), nine for sort (95.24%) and two for REP copy (99.64%). These percentages cover the sampler's eligible scalar loads. REP transfers, SIMD loads and other ineligible memory accesses are outside this denominator. Each list is bound to the SHA-256 of its original ELF. [Registered profiles and calibration totals](../experiments/live_sampler_fast/profiles.json)

No observation from the new guest contributed to the selection. We repeated the original seven inputs and added a 256 KiB dependent walk, a larger sort and a longer copy. The two scaled cases use the exact inputs and outputs in Aonohako's published C++20 corpus. The three inputs test transfer to new inputs of the same executables. A newly compiled submission needs its own profile. [Inputs and independent walk oracles](../experiments/live_sampler_fast/cases.json) · [Premeasurement registration](https://github.com/seo-rii/durante/commit/488707082bf1e4ddb78d9f4249e895bc1673771d)

All six arms run with the release v11 live work clock, x86-64-v2 CPUID, a 5 ms owner poll and the same nonbinding budget. Each input runs once natively to warm up, followed by six interleaved repetitions of each arm. The Linux benchmark cgroup and affinity constrain the owner and its child to CPU 1. The tracing controller uses CPU 0. Timings measure child user plus system CPU through termination, including writing the different reports. Prelaunch ELF hashing and profile lookup in the parent and offline decoding are outside this CPU figure. The profiled launcher checks the ELF immediately before each selected run. [Guest collector](../experiments/live_sampler_fast/bench.py)

The registered gates require a saving of at least 3% over Full in aggregate, with at most 2% regression on any input, and a separate overhead comparison allowing 5% over Live pooled and 10% per input. We also required the selected sites to account for at least 90% of Full's eligible visits in each input and repetition. Accuracy at predicting native CPU time was outside these gates. [Evaluation plan](../experiments/live_sampler_fast/design.json)

The follow-up measured first-run profile construction on five new executables and a control that changes its hot loop between inputs. [First-run costs and coverage](GCE_FIRST_RUN_SAMPLER.md)

## Offline verification and use

The offline verifier rebuilds both manifests from the earlier receipts, verifies the schedule, raw process records, terminal clocks and CPU placement, and checks the original executable archive if provided. The scorer compares the binary and JSON reports, registered projections, cold and hot walk sites, coverage and cost. From the Durante checkout with Python 3.11 or later run the following.

```sh
study=experiments/live_sampler_fast
data=experiments/results/gce-live-sampler-fast
python3 "$study/verify.py" "$data" --elf-archive "$tmp/elfs.tar.xz"
python3 "$study/score.py" "$data/dataset.json.gz" "$data/receipts.tar.xz" --verify "$data/evaluation.json"
python3 -m unittest discover -s tests -p 'test_live_sampler_fast*.py'
```

The packed file starts with `DSPAN001`, ten little-endian 64-bit header fields and six 64-bit fields per executed site. The header includes the original schema, PID, thread count, sampling period, line size, four global counters and row count. Each row contains the relative PC, loads, total pairs, near and far pairs, and the sampled address span. To convert a capture to the original schema-2 JSON or resolve the sorted decimal offsets for an exact registered executable, use the two commands below. The resolver returns an error for an unknown ELF.

```sh
python3 "$study/probe/packed.py" sample.bin
python3 "$study/profiles.py" --sites-for /path/to/executable
```

For example, the resolver returns `6656` for the archived pointer-walk ELF. The profiled client accepts `-out <new-report-path> -sites 6656` as its DynamoRIO client options. On input `65536 2097573`, the direct client run returns program output `38952` and one site with 2,097,573 loads, 2,049 far pairs and a span of 259,284 bytes. The guest collector shows the full release live-clock launch, including the v11 options and both client IDs. [Launch arguments](../experiments/live_sampler_fast/bench.py)

Use the release installation, corrected DynamoRIO 11.3.0-1 runtime, original `libaccess_span.so`, Aonohako ELF archive and `libsampler_gate.so` described in the [preceding build instructions](GCE_LIVE_SAMPLER_COST.md#reproduction). Build the three new research clients on Linux x86-64 with the same GCC 12.2 toolchain. The materializer validates every input against the registered digests. On a disposable Milan guest with one thread per core, run `sudo taskset -c 0 ./guest.sh` from the prepared payload.

```sh
cmake -S "$study/probe" -B "$tmp/fast" -DCMAKE_BUILD_TYPE=Release -DDynamoRIO_DIR="$DynamoRIO_DIR"
cmake --build "$tmp/fast"
python3 "$study/probe/check_controls.py" --drrun "$release/dynamorio/bin64/drrun" \
  --meter "$release/lib/libdurante.so" --span "$span_build/libaccess_span.so" --build "$tmp/fast"
python3 "$study/prepare.py" "$tmp/payload" --release "$release" --elf-archive "$tmp/elfs.tar.xz" \
  --span "$span_build/libaccess_span.so" --gate "$cost_build/libsampler_gate.so" \
  --catalog "$tmp/fast/libsampler_catalog.so" --packed "$tmp/fast/libsampler_packed.so" \
  --profiled "$tmp/fast/libsampler_profiled.so"
```
