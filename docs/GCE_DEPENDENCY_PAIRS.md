# Separating divide dependencies and memory access order on Compute Engine

**The same Durante counters corresponded to an 11.35-fold difference in native CPU time.** In the follow-up, two serial loops visited the same 16 MiB table with identical initialization and loop code. Reading adjacent entries took 116.70 ms. Following scrambled successors took 1,324.76 ms. The Rust clock estimated 114.71 ms for both. A separate divide control found that two instructions that zero the same register give almost identical native times and substantially different modeled times. [Follow-up measurements](../experiments/results/gce-dependency-pairs/followup/evaluation.json)

The preceding Rust study overestimated GCD by 56.68% and underestimated binary search by 38.15%. Its fitted clock charges 0.319429 ns per modeled cycle and 0.003405 ns per nominal memory operand byte. Both experiments here use its exact frozen coefficients. They test mechanisms with controlled assembly. The original GCD and search timing errors come from the earlier Rust study. [Rust results](GCE_RUST_OPERAND_BYTES.md)

## Changing dependencies and working set

| Workload | Native CPU, serial / four chains | Frozen estimate, serial / four chains | Native ratio | Predicted ratio |
| --- | ---: | ---: | ---: | ---: |
| 9-bit dividend | 116.06 / 64.00 ms | 246.52 / 225.08 ms | 1.81× | 1.10× |
| 31-bit dividend | 136.71 / 63.99 ms | 246.52 / 225.08 ms | 2.14× | 1.10× |
| 16 KiB successor table | 112.08 / 29.10 ms | 114.86 / 29.45 ms | 3.85× | 3.90× |
| 16 MiB successor table | 1,554.25 / 300.13 ms | 120.28 / 34.86 ms | 5.18× | 3.45× |

Each divide program executes 33,554,432 unsigned 32-bit divisions by 251. The next dividend is the previous remainder plus a constant. Both constants are congruent modulo 251. This gives the two input magnitudes the same remainder stream. Four interleaved chains partition a single stream into contiguous quarters. They divide exactly the same multiset of dividends as the serial loop. Larger dividends cost 17.79% more native CPU in the serial loop. The two four-chain medians differed by 0.02%. The static clock produced identical results across magnitudes. [Verified ELF instruction listings, inputs and checksums](../experiments/results/gce-dependency-pairs/fixtures.json)

Each table program performs 71,307,264 four-byte successor loads after it constructs one scrambled cycle. Serial and four-chain programs visit the same addresses with the same multiplicity. Within each size or dividend magnitude, their executable text differs in just six bytes that select registers in the hot loop. All four diagnostic observations in all eight cases matched the exact instruction and nominal operand-byte counts derived from the assembly. Only the two 16 KiB cases met the frozen ±20% timing target. Each native control exceeded 20 ms. The 16 MiB serial case had 21.26% spread, above the registered 10% gate. Even using its fastest observation and the slowest 16 KiB serial observation, the ratio was 12.16×. This first cross-size comparison includes different table construction costs. [First comparison and native ranges](../experiments/results/gce-dependency-pairs/evaluation.json)

## Follow-up on a second VM

### Holding memory setup and footprint constant

On a second pinned VM, each eight-byte record of a 16 MiB table held two four-byte successor indices. One field led to the adjacent entry and the other to the next entry in a fixed scrambled cycle. Every executable initialized both fields in the same order using byte-identical code. At a given chain count the hot loop was also byte-identical. Only the selected field and starting indices changed. The measured loop performed 69,206,016 loads, visiting every record exactly 33 times in total. The two fields in each record share a cache line and page. [Assembly](../experiments/dependency_pairs/followup/locality.S) · [Executable identities](../experiments/results/gce-dependency-pairs/followup/fixtures.json)

| Load dataflow | Adjacent native | Scrambled native | Native ratio | Estimate for either order |
| --- | ---: | ---: | ---: | ---: |
| One chain | 116.70 ms | 1,324.76 ms | 11.35× | 114.71 ms |
| Four chains | 36.34 ms | 280.31 ms | 7.71× | 31.81 ms |

Every follow-up load diagnostic recorded 143,654,958 instructions and 293,601,288 nominal operand bytes. The latency counter was exactly equal across the two orders at each chain count. Even a nonlinear clock using only these counters must therefore return the same time for both. The serial scrambled controls varied from 1,106.13 to 1,642.56 ms (40.49% spread). Even their fastest observation was 9.41× the slowest adjacent observation. Both four-chain rows met the 10% spread gate. Their corresponding conservative ratio was 7.24×, forcing at least 75.71% error on one of those two workloads. For a ratio `r > 1`, the best common estimate has worst relative error `(r − 1)/(r + 1)`. [Raw controls](../experiments/results/gce-dependency-pairs/followup/dataset.json.gz) · [Range bounds](../experiments/results/gce-dependency-pairs/followup/evaluation.json)

### Holding division inputs constant

We replaced the four `xor %edx,%edx` instructions in the unrolled divide loop with `mov $0,%edx`. Both clear the high half of the dividend, and the loop discards the resulting flags. A local counter-only control first identified a difference in modeled register dependencies. We then froze the comparison and measured both spellings natively on the second GCE VM. The 31-bit dividends, divisor, remainder sequence, checksum, instruction count and operand bytes stayed fixed within each chain count. [Counter-only control](../experiments/results/gce-dependency-pairs/zeroing-control.json) · [Follow-up protocol](../experiments/dependency_pairs/followup/protocol.json)

| Divide dataflow | XOR native | MOV native | XOR estimate | MOV estimate |
| --- | ---: | ---: | ---: | ---: |
| One chain | 136.54 ms | 136.60 ms | 246.52 ms | 246.52 ms |
| Four chains | 63.80 ms | 63.83 ms | 225.08 ms | 61.63 ms |

For four chains the native medians differed by 0.04%, while the estimate fell by 72.62%. The version 1 dependency model reads the previous value of EDX when processing a same-register XOR. A move from an immediate has no register source. This explains the change in the **estimate**. Removing that modeled dependency alone would still predict a 4.00× serial/parallel ratio with MOV, against the observed 2.14×. The modeled fixed 20-cycle DIV cost and the throughput of independent divides warrant separate validation. [Model implementation](../client/latency_bound.h) · [Native and diagnostic results](../experiments/results/gce-dependency-pairs/followup/evaluation.json)

The two controls identify missing information relevant to the Rust errors. Actual binary search also branches and GCD uses changing divisors. These results do not apportion the original Rust program times. A candidate grading clock would need an affordable order-sensitive measure of memory work and separate tests of divide latency and throughput before being applied to verdicts.

## Collection and verification

Each study used its own temporary Seoul AMD EPYC 7B13 VM. Each round rotated the order of the four pairs and reversed which variant ran first. For each of 32 triplets on each VM, a native child ran before and after the diagnostic child. The measured time is the reaped child's user plus system CPU time from `wait4`. Tables use the median of eight native observations and the median of four diagnostic predictions per case. Every child used the frozen ELF, empty input and an eight-byte expected checksum. Only the diagnostic used the x86-64-v2 CPUID profile. Both guests reported a 32 KiB L1 data cache, 512 KiB L2 and 32 MiB shared L3. These identifiers give the scale of the working sets. No hardware cache-miss counts or DRAM accesses were observed. [First protocol](../experiments/dependency_pairs/protocol.json) · [Follow-up protocol](../experiments/dependency_pairs/followup/protocol.json)

The guest scheduler traces placed all 192 timed children on CPU 1. The two CPU 0 observers made 169 and 159 polls respectively. Both traces reported zero drops and CPU 1 accumulated zero guest steal ticks during collection. Each VM and its boot disk were deleted and returned HTTP 404 on lookup. [First platform and teardown](../experiments/results/gce-dependency-pairs/platform.json) · [Second platform and teardown](../experiments/results/gce-dependency-pairs/followup/platform.json)

The archives contain the original executables, stdout, wait4 receipts and Durante records. For the primary GCC build, a temporary object basename in an ELF file symbol can change the full file digest on rebuild. Use the archived ELF for byte-identical replay. Run the following commands from the repository root.

```sh
study=experiments/dependency_pairs
data=experiments/results/gce-dependency-pairs
python3 "$study/score.py" "$data/dataset.json.gz" --verify "$data/evaluation.json"
python3 "$study/verify_receipts.py" "$data/dataset.json.gz" "$data/samples.tar.xz"
python3 "$study/verify_placement.py" "$data/dataset.json.gz" "$data/placement.json.gz" --platform "$data/platform.json"
python3 "$study/zeroing_control.py" --verify "$data/zeroing-control.json"

second="$data/followup"
python3 "$study/followup/replay.py" score "$second/dataset.json.gz" --verify "$second/evaluation.json"
python3 "$study/followup/replay.py" receipts "$second/dataset.json.gz" "$second/samples.tar.xz"
python3 "$study/followup/replay.py" placement "$second/dataset.json.gz" "$second/placement.json.gz" --platform "$second/platform.json"
```
