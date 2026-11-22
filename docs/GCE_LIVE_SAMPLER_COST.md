# Where the live address sampler spends CPU

Linking the original meter and sampler into a single DSO saved **0.76%** of child CPU relative to the two-client version. It added **11.95%** relative to the release live clock. Both figures missed the registered thresholds. The largest increase appeared when enabling per-site instrumentation and visit counting. [Scored results](../experiments/results/gce-live-sampler-cost/evaluation.json)

## Results on the pinned CPU

| Comparison | CPU over 42 runs, baseline → candidate | Difference in CPU | Relative change |
| --- | ---: | ---: | ---: |
| Live → Empty | 7.366817 → 7.378776 s | +11.959 ms | +0.16% |
| Empty → Gate | 7.378776 → 8.177807 s | +799.031 ms | +10.83% |
| Gate → Full | 8.177807 → 8.310045 s | +132.238 ms | +1.62% |
| Live → Full | 7.366817 → 8.310045 s | +943.228 ms | +12.80% |
| Full → Fused | 8.310045 → 8.247227 s | −62.818 ms | −0.76% |
| Live → Fused | 7.366817 → 8.247227 s | +880.410 ms | +11.95% |

The Empty → Gate increase accounted for 84.71% of the 943.228 ms difference between Live and Full. That step enables translation-time site selection, injected code, countdowns, minimal callbacks and per-site report generation. Gate → Full also changes argument handling and serialization. This study measures the combined changes at each step. Fused saved 0.10% at the median paired ratio against Full, with individual ratios ranging from an 18.09% saving to a 16.16% increase. It satisfied the per-input regression limit on all seven inputs and missed the pooled 3% savings gate. Its overhead against Live exceeded 10% on five inputs, reaching 17.31% on the 32 KiB walk. [Case and repetition data](../experiments/results/gce-live-sampler-cost/dataset.json.gz)

All 42 Gate–Full and all 42 Full–Fused comparisons agreed on instruction offsets, eligible load visits and completed sample pairs. All 90 walk-oracle checks passed, including 60 full-span reports. On other instructions 36 of 29,280 paired site comparisons differed in span, and none differed in near/far pair counts. Across the 42 Full–Fused pairs both REP byte totals always agreed. One 16 MiB repetition had 16 fewer instructions with Fused. The source of that cross-process variation was not identified. Each of the 210 live clocks matched its own canonical process records. [Archived process records](../experiments/results/gce-live-sampler-cost/receipts.tar.xz)

The kernel recorded all 469 measured exec events on CPU 1 over the 259 runs. Guest CPU 1 reported zero steal ticks and the trace recorded zero losses. The temporary VM and disk both returned 404 after deletion, and the temporary SSH key was removed. [Kernel trace](../experiments/results/gce-live-sampler-cost/exec-trace.txt) · [Teardown receipt](../experiments/results/gce-live-sampler-cost/cleanup.json)

A later run tested a translation-only control, compact all-site output and sampling only the busiest instructions of each exact ELF. [Optimized sampling and held-out coverage](GCE_LIVE_SAMPLER_FAST.md)

## Implementations and measurement

The preceding C++20 study found that adding the span sampler to the release v11 work clock increased child CPU by 13.94%. Recording extrema in the existing diagnostic sampler had cost 0.12%. This experiment repeats the live comparison and tests the cost of loading a second client, executing a sampling countdown and producing address telemetry. A single-client candidate also links the original meter and sampler into one DSO. [Earlier measurements](GCE_CPP20_LOCALITY.md) · [Premeasurement registration](https://github.com/seo-rii/durante/commit/4397d1979b78291f6f30acc0a322808f61312f35)

| Arm | Work performed |
| --- | --- |
| Native | Execute the application directly. |
| Live | Run the release v11 work clock with the x86-64-v2 profile and instruction and REP-byte accounting. |
| Empty | Also load a second client, initialize its thread and process hooks, open its report and write its completion receipt. |
| Gate | The second client instruments eligible scalar reads, maintains the per-site countdown, executes the scheduled callbacks and writes per-site visit and pair counts. |
| Full | The original second client also takes effective address samples, classifies near and far pairs and writes per-site spans. |
| Fused | Load the same v11 meter and full span sampler in a single research DSO. Its wrapper calls both original initializers. |

Both full implementations use the pre-existing compiled component code. The v11 release used GCC 14.2 and the sampler used GCC 12.2. The candidate retains their original relocatable objects, renames the two initialization symbols and links a wrapper. The runtime supplies the ordinary v11 options and a separate report path. The release worker continues to use its ordinary client. [Build and wrapper](../experiments/live_sampler_cost/probe)

The seven inputs, their static ELF files, the guest release binaries, the per-case warmup and the six-position interleaving follow the earlier study. On a Seoul Compute Engine AMD Milan guest we constrained the collector and its direct children to CPU 1. The tracing controller ran on CPU 0. Every live run used the same 5 ms polling interval, 100-billion-unit work budget and 120 s outer deadline. Each timing is the child's native `wait4` user plus system CPU, including dynamic translation and report generation. The observer's CPU and end-to-end grading latency fall outside this measurement. [Collector](../experiments/live_sampler_cost/bench.py) · [Protocol](../experiments/live_sampler_cost/protocol.json)

The cost gate requires at most 5% overhead over Live across all seven inputs and at most 10% on each input. The separate optimization gate requires the single-client candidate to save at least 3% against Full in aggregate, with at most 2% regression on any input. Each pool divides the sum of child CPU in one arm by the sum in the other over the same six repetitions. The adjacent comparisons include changes to report contents and shutdown. [Analysis plan](../experiments/live_sampler_cost/design.json)

## Reproduction

From the Durante checkout, Python 3.11 or later can verify the placement, outputs, original process records and terminal clocks, then independently recompute the CPU ratios and site comparisons. Passing `--elf-archive` to the verifier also checks the archived executables. [Original Aonohako ELF archive](https://github.com/seo-rii/aonohako/blob/b938afaa6c171a8a56e24d376d6662f93fcea4c6/docs/durante/gce-v11-cpp20-accuracy/corpus/elfs.tar.xz)

```sh
study=experiments/live_sampler_cost
results=experiments/results/gce-live-sampler-cost
python3 "$study/verify.py" "$results"
python3 "$study/score.py" "$results/dataset.json.gz" "$results/receipts.tar.xz" --verify "$results/evaluation.json"
python3 -m unittest discover -s tests -p 'test_live_sampler_cost*.py'
```

For a new measurement use the release installation, corrected SDK and the seven frozen inputs from the previous study. Keep the v11 `CMakeFiles/durante.dir/durante.c.o` from its GCC 14.2.0 (Debian 14.2.0-19) build and the `CMakeFiles/access_span.dir/access_span.c.o` from the GCC 12.2.0 (Debian 12.2.0-14+deb12u1) sampler build. The new CMake build checks both object digests before linking. The reference v11 build used CMake 3.31.6 and GNU binutils 2.44. The span and research client builds used CMake 4.0.3 and GNU binutils 2.40 with the GCC 12.2 toolchain. [Release and sampler build instructions](GCE_CPP20_LOCALITY.md#reproducing-the-measurements)

```sh
cmake -S "$study/probe" -B "$tmp/cost" -DCMAKE_BUILD_TYPE=Release -DDynamoRIO_DIR="$DynamoRIO_DIR" \
  -DV11_OBJECT="$v11_build/CMakeFiles/durante.dir/durante.c.o" \
  -DSPAN_OBJECT="$span_build/CMakeFiles/access_span.dir/access_span.c.o"
cmake --build "$tmp/cost"
python3 "$study/probe/check_controls.py" --drrun "$release/dynamorio/bin64/drrun" \
  --meter "$release/lib/libdurante.so" --span "$span_build/libaccess_span.so" --build "$tmp/cost"
python3 "$study/prepare.py" "$tmp/payload" --release "$release" --elf-archive "$tmp/elfs.tar.xz" \
  --span "$span_build/libaccess_span.so" --empty "$tmp/cost/libsampler_empty.so" \
  --gate "$tmp/cost/libsampler_gate.so" --fused "$tmp/cost/libsampler_fused.so"
```

Copy the materialized payload to a disposable `n2d-standard-4` guest with one thread per core. From that directory run `sudo taskset -c 0 ./guest.sh`. The script applies the CPU 1 cgroup for every trial and writes the per-process receipts and trace to `output/`. The payload builder validates every staged binary and source digest. [Payload builder](../experiments/live_sampler_cost/prepare.py) · [Capture exporter](../experiments/live_sampler_cost/export.py)
