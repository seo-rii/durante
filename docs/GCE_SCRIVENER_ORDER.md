# Scrivener query order and Durante timing

The study uses the released clients from Durante #96 and #97 and the two Scrivener executables from #95. An earlier experiment found that binary lifting finished faster on interwoven appends and queries even though the instruction count and nominal memory traffic rose. Here each new input builds exactly the same 180,000-character document, then requests the same 180,000 positions. Only the order of those requests changes. The fourth input repeats the earlier interwoven workload. [Design and hashes](../experiments/scrivener_order/plan.json) · [Earlier counterexample](GCE_CONTEST_SUBMISSIONS.md)

Ascending positions group adjacent queries. The tiled input shuffles the sorted queries within groups of 256. The shuffled input permutes the whole list. All three request 113,621 distinct positions. They take exactly 5,733,748 query steps in the compact jump tree, or 1,538,346 in the binary-lifting implementation. A step follows one ancestor during a lookup. Every case checks the actual solver output against the character sequence computed from the original text. [Corpus and step counts](../experiments/scrivener_order/corpus.py)

Both clients ran on the same byte-identical #97 engine on each guest. The #96 and #97 bundles contain different engine binaries, despite sharing a runtime patch revision. Holding the engine fixed makes the ordinary-mode CPU comparison specific to the client. Each executable/input pair received two alternating runs per client with instruction and REP counts enabled, one #97 diagnostic run adding latency and operand bytes, and nine direct native runs. Each child ran on CPU 1 of an AMD Milan N2D guest with one thread per core, its own 120-second watchdog and the image's default ASLR configuration. The two guests ran the same payload. [Collector](../experiments/scrivener_order/bench.py) · [#96 bundle](https://github.com/seo-rii/durante/actions/runs/45634013566) · [#97 bundle](https://github.com/seo-rii/durante/actions/runs/45640347158)

A follow-up forecasts the CPU time of three new query orders using simulated cache and branch misses. [Held-out cache predictions](GCE_SCRIVENER_CACHE.md)

## Measured order effects

Changing ascending queries to shuffled queries increased the compact tree's native median by **93% and 87%** on the two guests. Binary lifting slowed by **24% on both**. The four measured totals each changed by less than 0.25 parts per million for both executables. Matching the lookup count and total ancestor steps still leaves a reproducible timing difference. The experiment measures the effect of changing the query stream throughout the executable, including its parser, branches and memory accesses. It collected no hardware events to apportion the difference among those operations. [Boot 1 metrics](../experiments/results/gce-scrivener-order/metrics.json) · [Boot 2 metrics](../experiments/results/gce-scrivener-order-repeat/metrics.json)

| Native user + system CPU, median of nine (ms) | Ascending | Tiled | Shuffled | Shuffled / ascending |
|---|---:|---:|---:|---:|
| Compact jump tree, boot 1 | 35.734 | 41.312 | 69.082 | 1.93× |
| Compact jump tree, boot 2 | 35.655 | 40.380 | 66.824 | 1.87× |
| Binary lifting, boot 1 | 35.745 | 40.188 | 44.403 | 1.24× |
| Binary lifting, boot 2 | 35.670 | 40.201 | 44.361 | 1.24× |

For the compact tree, even the fastest shuffled run on boot 1 took 66.140 ms, while the slowest ascending run took 37.333 ms. For any nonnegative linear combination of instructions, REP bytes, modeled latency and operand bytes, the largest possible shuffled/ascending work ratio was 1.000000241 or less, allowing a nonnegative intercept. The corresponding lower bound on the worse of the two relative time errors was **27.84%** on boot 1 and **26.30%** on boot 2, rounding down. For binary lifting the bounds were 8.40% and 8.31%. The verifier uses the most favorable counts and times among the observations and allows for rounding the prediction to nanoseconds. This diagnostic applies to the four-counter linear model family. [Bound and replay](../experiments/scrivener_order/verify.py)

### Forecasting the held-out tiled order

For each executable and guest, the baseline scales the ascending native median by the tiled/ascending ratio of ordinary Durante work. The order-sensitive forecast uses the measured ascending and shuffled medians as endpoints. Its position between them comes from the change in requested character positions in the input. Summing the bit length of each absolute position change gives 161,309 for ascending, 1,148,070 for tiled and 2,843,163 for shuffled. This places tiled 36.79% of the way from ascending to shuffled. The midpoint control uses the same two paid endpoints. The tiled native measurements enter only when scoring the fixed forecasts. The position statistic relies on the Scrivener text format.

| Tiled case | Native (ms) | Ascending work forecast | Order-sensitive forecast | Two-endpoint midpoint |
|---|---:|---:|---:|---:|
| Compact, boot 1 | 41.312 | 35.734 (−13.5%) | 48.004 (+16.2%) | 52.408 (+26.9%) |
| Compact, boot 2 | 40.380 | 35.655 (−11.7%) | 47.123 (+16.7%) | 51.2395 (+26.9%) |
| Lifting, boot 1 | 40.188 | 35.745 (−11.1%) | 38.931 (−3.1%) | 40.074 (−0.3%) |
| Lifting, boot 2 | 40.201 | 35.670 (−11.3%) | 38.868 (−3.3%) | 40.016 (−0.5%) |

The position statistic reduced error for lifting and increased it for the compact tree on both boots. The midpoint had the smallest lifting errors. The ascending and order-sensitive forecasts agreed with all 14 fixed-limit native majorities per boot. The midpoint agreed with 13/14, predicting a timeout for the compact tree at 50 ms despite nine native passes. These are deadline predictions from completed runs. [Boot 1 observations](../experiments/results/gce-scrivener-order/output.tar.gz) · [Boot 2 observations](../experiments/results/gce-scrivener-order-repeat/output.tar.gz)

### Release comparison and collection cost

All 16 paired #96/#97 runs per boot returned identical ordinary instruction and REP counts. Those counts also matched the #97 diagnostic run for every pair. For the earlier interwoven input, comparing the new four-counter diagnostics against #95 gives −4 instructions and −16 sixteenths of a modeled cycle on the compact tree. Lifting gives +35 instructions, +140 sixteenths of a cycle and +32 operand bytes. REP totals matched. These small differences repeated on both guests. The historical comparison includes different runtime builds and launch environments. [Prior study and control](GCE_CONTEST_SUBMISSIONS.md)

Summing ordinary instrumented child CPU time across the 16 runs per client gives 4.065 s → 4.066 s on the first boot and 4.082 s → 4.043 s on the repeat. Across the eight executable/input pairs, the median paired previous/current ratio was 1.004× and 1.008× respectively. The effect on total CPU changed direction across boots. The current ordinary profile cost a median of 4.19× and 4.22× native CPU. Adding the latency and operand-byte diagnostics cost a further 16.59× and 16.52× relative to ordinary mode. These ratios include program startup, two ordinary samples per client and one diagnostic sample per case. Both traces covered all 112 measured processes per boot with zero CPU steal ticks. Both temporary VMs, disks and private SSH keys were removed. [Boot 1 collection](../experiments/results/gce-scrivener-order/collection.json) · [Boot 2 collection](../experiments/results/gce-scrivener-order-repeat/collection.json)

## Reproduce

From the repository root with Python 3.11 or later, replay both complete raw journals. The verifier checks the kernel exec trace, CPU accounting, process records, input bytes and oracle hashes. The test suite independently recomputes the paired costs, forecast errors, and fixed-deadline votes.

```sh
tmp=$(mktemp -d)
for suffix in '' '-repeat'; do
    result="experiments/results/gce-scrivener-order${suffix}"
    work="$tmp/boot${suffix}"
    mkdir "$work"
    tar xzf "$result/output.tar.gz" -C "$work"
    python3 -B experiments/scrivener_order/verify.py "$work/output" --output "$work/metrics.json"
    cmp "$result/metrics.json" "$work/metrics.json"
done
python3 -B -m unittest discover -s tests -p 'test_scrivener_order*.py' -v
```

The guest builder takes the `bundle.tar.gz` from each registered Actions artifact and checks its SHA-256 before using it. It rebuilds both static workloads with GCC and checks their frozen ELF digests. Copy the resulting directory to a disposable Debian guest with the registered CPU topology and run `sudo taskset -c 0 ./guest.sh` inside it. [Guest builder](../experiments/scrivener_order/prepare.py)

```sh
python3 -B experiments/scrivener_order/prepare.py "$tmp/guest" \
    --previous-bundle "$bundle96" --current-bundle "$bundle97"
```
