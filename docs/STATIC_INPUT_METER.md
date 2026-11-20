# Cost of live accounting after static programs read input

On v8 the histogram stack benchmark took 2.76 s of CPU on a Cloud Run worker with live accounting and 46.45 ms natively. A local control isolated most of that cost to the ordinary meter. Eligible static basic blocks first translated during startup used compact instrumentation for the lifetime of their cached translation. The histogram program enters a multi-block loop shortly after parsing stdin. [Original worker and local controls](CPP20_CROSSED_MEMORY.md#cost-of-the-ordinary-live-meter)

For the ordinary live clock, a thread that received a positive byte count from a read or readv on descriptor zero can now use inline instrumentation for eligible newly translated static blocks after 16,384 charged instructions. Other blocks keep their existing selection rules, including the 131,072-instruction threshold and the self-loop fast path. Stored translations and the existing static executable text checks apply. This affects the CPU cost of collecting work. The time budget still uses instructions and typed REP bytes.

## Paired measurements

The local benchmark ran five alternating pairs on a pinned AMD EPYC 7B12. Both arms used the same launcher, actual program path, inputs and 32 MiB runtime settings. For all 110 runs the candidate reproduced expected stdout, all terminal work fields and the shared live instruction, MOVS and STOS counters. The histogram median fell by 86.3%. The short iostream program took 13.9 ms more. Small percentage changes on the other programs varied across the five pairs. [CPU samples, run order and workload identities](../experiments/results/cpp20-static-input/local.json)

| Local program | Previous median CPU | Updated median CPU |
|---|---:|---:|
| Histogram stack | 1,960.65 ms | 268.31 ms |
| Count distinct subarrays | 130.77 ms | 137.13 ms |
| Sparse-table range minimum | 183.40 ms | 187.32 ms |
| Short C++ iostream read and print | 94.86 ms | 108.73 ms |
| Short scanf and printf | 60.47 ms | 59.73 ms |
| Eight-instruction assembly startup | 10.76 ms | 10.82 ms |

The complete local record includes the integer, branch, copy and fill controls, including a one-iteration integer read. The three short diagnostic fixture sources and their inputs and outputs are embedded in their individual records. To reproduce a pair for a given executable, use the two Release clients built against the same DynamoRIO 11.3 development headers and the same patched launcher. The historical client source is the parent of commit `2795424`. [Measurement runner](../experiments/l1d_probe/compare.py)

```sh
python3 -B experiments/l1d_probe/compare.py --ordinary-live --rounds 5 --cpu "$cpu" \
  --drrun "$sdk/bin64/drrun" --baseline "$old_client" --candidate "$new_client" \
  --output "$new_results_directory" --case histogram-stack "$program" "$input" "$expected_stdout"
```
