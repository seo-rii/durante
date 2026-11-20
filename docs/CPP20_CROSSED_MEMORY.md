# C++20 clock calibration across copy traffic and working-set sizes

The existing six-counter fit predicted two of three previously unseen Cloud Run CPU medians within 20%. Adding six calibration inputs reduced that to one of three. The extra inputs showed that even an optimally chosen set of nonnegative weights must miss at least one of the 21 calibration medians by 29.47%. The expanded fit is unsuitable for setting submission time limits. [Sealed predictions](../experiments/results/cpp20-crossed-memory/predictions.json) · [Minimax certificates](../experiments/results/cpp20-crossed-memory/bounds.json)

## Separating copy traffic from ordinary memory accesses

All six calibration inputs use one static executable. Each initializes the same 16 MiB array, makes the same 8,388,608 seeded table reads, then issues 32,768 REP MOVSB operations between the same two 8 KiB buffers. The input selects the range of table indices and whether each copy transfers 0 or 8,192 bytes. Each time below is the median of six uninstrumented process CPU readings. The simulated L1D misses come from a separate deterministic replay of the whole executable. [Fixture and independent oracles](../experiments/crossed_memory_corpus/manifest.json) · [Calibration runs](../experiments/results/cpp20-crossed-memory/calibration.json) · [Replayed counters](../experiments/results/cpp20-crossed-memory/features.json)

| Read footprint | CPU, 0 MiB copied | CPU, 256 MiB copied | L1D misses, 0 MiB | L1D misses, 256 MiB |
|---|---:|---:|---:|---:|
| 4 KiB | 29.82 ms | 31.36 ms | 262,840 | 262,839 |
| 1 MiB | 30.40 ms | 31.76 ms | 8,388,586 | 8,388,840 |
| 16 MiB | 50.54 ms | 47.61 ms | 8,634,894 | 8,635,148 |

Adding 256 MiB of warm copies changed the simulated misses by at most 254. Increasing the no-copy read footprint from 1 to 16 MiB increased native median CPU by 66.3%. Every measured model input grew by at most 2.94% between those inputs. With nonnegative weights, their weighted sum can grow by at most the same factor. Even allowing a 20% overestimate for the medium input gives a large-input prediction of at most 37.55 ms. A prediction within 20% of the large median needs at least 40.43 ms. This pair alone explains why adjusting weights cannot match both medians.

Cloud Run also varied within cases. Five of the six calibration inputs exceeded the 10% native spread limit. Taking the largest observed medium and smallest observed large times weakens the pairwise error lower bound from 23.53% to 14.92%. These are finite sample ranges on one instance. The experiment did not collect hardware cache or memory latency counters, so it cannot attribute the native difference to a specific cache level. [Native CPU observations](../experiments/results/cpp20-crossed-memory/calibration.json)

## Prospective grading decisions

Both fits use instructions, MOVS bytes, STOS bytes, loop recurrence, gshare mispredictions and simulated L1D misses. They use the same nonnegative relative least-squares estimator. The control uses the original 15 medians. The expanded fit uses those 15 and all six new medians. It reduced the cost per simulated miss from 2.997 to 1.393 ns and raised the cost per MOVS byte from zero to 0.0327 ns. Both fits and all external predictions were sealed before any of the blind submissions reached the worker. [Experiment protocol](../experiments/results/cpp20-crossed-memory/protocol.json) · [HTTP request protocol](../experiments/results/cpp20-crossed-memory/request-protocol.json)

| Blind program | Native CPU median | Native spread¹ | Original fit | Expanded fit | Expanded error |
|---|---:|---:|---:|---:|---:|
| Count subarrays with distinct values | 24.61 ms | 21.1% | 66.21 ms | 41.79 ms | +69.8% |
| Largest rectangle in a histogram | 46.45 ms | 5.7% | 54.66 ms | 51.60 ms | +11.1% |
| Sparse-table range minimum | 39.15 ms | 6.6% | 34.85 ms | 21.46 ms | −45.2% |

¹ The range of six uninstrumented CPU readings divided by their median. Every native sample exceeded 20 ms. All three local replays had identical counter vectors for each program. The sparse-table fit missed even though its native measurements met the stability gate. [Blind CPU triplets and verdicts](../experiments/results/cpp20-crossed-memory/cloud.json)

At 31 ms all three native sparse-table submissions timed out. The expanded model predicted acceptance. The original fit predicted TLE, and the ordinary v8 clock actually stopped the submission. At 33 ms all three distinct-window submissions were accepted and the expanded model predicted TLE. At 30 ms even the repeated native distinct-window verdicts split, with two accepts and one TLE. At the six limits centered on native medians, the expanded model matched all three native verdicts four times, matched one once and disagreed unanimously once. The ordinary live clock and the control fit each had five unanimous matches and one mixed comparison.

The HTTP test used one private one-vCPU Gen2 revision in Seoul. All 123 requests landed on one instance, with 54 calibration and 69 blind executions. The ordinary v8 clock made its actual budget decisions using three counters with 90 s CPU and 120 s wall watchdogs. The six-counter predictions were scored offline. The service, revision and temporary image package each returned HTTP 404 after deletion. [Request placement](../experiments/results/cpp20-crossed-memory/placement.json) · [Cleanup receipt](../experiments/results/cpp20-crossed-memory/cleanup.json)

## Cost of the ordinary live meter

On Cloud Run, the histogram submission used a median 2,761.9 ms of CPU under the ordinary live meter for a native median of 46.45 ms. The other two programs took 3.59 and 4.49 times their native medians. Measuring real time consumed by the meter separately matters when choosing an outer safety deadline. [Instrumented CPU triplets](../experiments/results/cpp20-crossed-memory/cloud.json)

A local follow-up ran five matched rounds per program using native execution, the bare DynamoRIO engine, the archived ordinary client and the same client with live reporting. On the histogram input those arms used median CPU times of 39.1, 55.7, 1,685.1 and 1,818.9 ms. None of these arms enabled cache simulation. This localizes most of the slowdown to ordinary instrumentation on this input. A separate benchmark measures the cost of the optional cache simulator. [Ordinary controls and run order](../experiments/results/cpp20-crossed-memory/ordinary-cost.json) · [Disjoint-copy cache probe benchmark](../experiments/l1d_probe/README.md#long-disjoint-copies)

The follow-up changes the live meter’s instrumentation choice after a static program reads stdin and measures both the histogram improvement and startup costs. [Static-input live meter measurements](STATIC_INPUT_METER.md)

## Reproduce the comparison

Run from the repository root. Numerical fitting and bound optimization require NumPy and SciPy. Scoring the frozen predictions uses the Python standard library. Use a fresh temporary directory for the generated files.

```sh
data=experiments/results/cpp20-crossed-memory
corpus=experiments/crossed_memory_corpus
out=$(mktemp -d)
calibration_sha=5a5103d1f03ff03029ed44e511224470f94db735fe644019ab53d3ad8abedfdb
features_sha=5b42ad6f999b8c921224ef863d08545df7fd5c8484adb7e938bf78f4d4461d01
python3 experiments/crossed_memory_model.py "$data/features.json" "$data/calibration.json" "$corpus/manifest.json" \
  --calibration-sha256 "$calibration_sha" --output "$out/predictions.json"
cmp "$out/predictions.json" "$data/predictions.json"
python3 experiments/score_memory_cost.py --crossed-memory "$out/predictions.json" "$data/cloud.json"
python3 experiments/crossed_memory_bounds.py "$data/features.json" "$data/calibration.json" "$corpus/manifest.json" \
  --features-sha256 "$features_sha" --calibration-sha256 "$calibration_sha" --output "$out/bounds.json"
```
