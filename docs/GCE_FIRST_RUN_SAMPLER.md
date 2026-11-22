# Learning a sampler profile on the first execution

On eight held-out Aonohako inputs, learning a profile on the first run reduced subsequent parent-plus-child CPU by **4.83%** relative to full sampling. Across all 48 paired trials the selected instructions accounted for **92.88% to 99.88%** of eligible scalar reads. On a different route through the same control executable, coverage fell to **0%** in all six repetitions. This makes a first-run profile useful for diagnostic sampling on repeated paths. A new input can change which instructions account for the work. [Evaluation](../experiments/results/gce-first-run-sampler/evaluation.json) · [Complete trial data](../experiments/results/gce-first-run-sampler/dataset.json.gz)

## Costs and coverage

| Held-out population | Paired comparisons | Profiled CPU vs Full | Profiled CPU vs Live | Eligible reads covered in each pair |
|---|---:|---:|---:|---:|
| Five Aonohako programs, eight inputs | 48 | −4.83% | −0.32% | 92.88–99.88% |
| Two paths in the control executable | 12 | −20.07% | +2.21% | 0% on the switched path, 99.94% on the repeated path |

The real inputs passed both preregistered CPU criteria. Their largest per-input increase over Live was 2.91%. When Live and Full can skip executable hashing, the estimated aggregate saving over Full becomes 4.65% and the difference from Live becomes −0.12%. The child-only saving over Full was 4.88%. These are sums over six balanced repetitions on one VM. The small difference from Live gives little basis to infer a speed advantage over the ordinary release clock. [Paired results and sensitivities](../experiments/results/gce-first-run-sampler/evaluation.json)

All 60 profiled runs exactly matched the corresponding Full run when restricted to their selected offsets, visit counts and completed sample-pair counts. The switched path illustrates the limit of that check. Full observed 4,194,719 reads at the new loop instruction on each run, plus 2,378 other eligible reads. Profiled emitted an empty site list. The original loop still covered every one of its 4,194,719 reads on the larger same-path input. These executions give a counterexample to reusing an ELF identity as evidence of coverage. This experiment measures instrumentation cost and does not establish a native-time prediction error. [Raw per-process receipts](../experiments/results/gce-first-run-sampler/receipts.tar.xz) · [Earlier native-time test](GCE_CPP20_LOCALITY.md)

The first full execution spent 2.71–3.58 ms of parent CPU selecting and writing its profile across the six executables. When that full run was already required, the observed mean saving recovered construction cost on the next input of each of the five real programs. Paying for an extra calibration execution changes the result. The table gives the number of subsequent required inputs to recover the cost, using the observed equally weighted held-out input mix for each ELF. Each estimate has a single calibration observation. [Calculation and assumptions](../experiments/first_run_sampler/design.json)

| Extra full calibration | Integer | Branch | REP fill | Modular inverse | Branch scan |
|---|---:|---:|---:|---:|---:|
| Common ELF hash | 10 | 15 | 8 | 7 | 21 |
| Full baseline can skip its hash | 10 | 16 | 9 | 8 | 23 |

## Which work was sampled

Coverage uses the full sampler's eligible scalar reads as its denominator. On the largest held-out input for each program, resolving the recorded module offsets against the original static ELF gives the following counts per Full run. To reproduce the mapping, add the lowest load-segment virtual address (0x400000 for these binaries) to each offset. Inspect the containing positive-size function ranges with `nm -nS -C --defined-only` and the instructions with `objdump -d -Mintel --disassemble=main`. [ELF archive](https://github.com/seo-rii/aonohako/blob/b938afaa6c171a8a56e24d376d6662f93fcea4c6/docs/durante/gce-v11-cpp20-accuracy/corpus/elfs.tar.xz) · [Per-site receipts](../experiments/results/gce-first-run-sampler/receipts.tar.xz)

| Input | Reads in program code / all eligible reads | Observed work |
|---|---:|---|
| `integer/4` | 2 / 17,324 | Two explicit reads from the stack in program code. Two runtime unwind functions account for 85.95% of eligible reads. |
| `branch/4` | 2 / 17,285 | Two reads from the stack. The same unwind functions account for 86.37%. |
| `mod_inverse/2` | 1 / 17,129 | One read from the stack. The unwind functions account for 86.93%. |
| `rep_fill/4` | 4,200,452 / 4,219,908 | The final checksum reads 4,194,304 bytes at offset 0x19a4. The v11 REP counter separately records the 8 GiB fill and 3,624 additional STOS bytes. |
| `branch_scan/2` | 16,777,220 / 16,796,707 | The loop tests 16,777,216 branch flags at offset 0x19fd. |

The two unwind functions are `classify_object_over_fdes` and `read_encoded_value_with_base`. Three of the five real profiles hit the 64-site cap while learning mainly runtime reads. A high fraction of eligible reads can therefore coexist with little information about a program's dominant computation. [Profiles and per-program totals](../experiments/results/gce-first-run-sampler/evaluation.json)

## Measurement

Five Aonohako C++20 executables had never supplied a site list in the preceding sampler experiments. Each first ran once with the complete scalar-read sampler. The launcher ranked the observed instruction offsets by visit count, retained the shortest prefix covering 95% of visits (up to 64 sites), and saved the list under the SHA-256 identity of that executable. An additional C++20 control contains two loops at different instruction offsets. The first input takes one loop. Its two held-out inputs repeat that loop at twice the size and take the other loop, respectively. [Premeasurement registration](https://github.com/seo-rii/durante/commit/975a8867e705d79203bad89ffe581143a7c3fb48) · [Controlled program and per-instruction oracle](../experiments/first_run_sampler/control)

All 186 runs produced their expected stdout and matching terminal clocks. The guest recorded zero steal ticks, and the execution trace retained every child with zero dropped events. Provider checks confirmed deletion of the temporary VM and boot disk. [Placement](../experiments/results/gce-first-run-sampler/placement.json.gz) · [Cleanup](../experiments/results/gce-first-run-sampler/cleanup.json)

The ten held-out inputs each ran under all six permutations of three modes. **Live** runs the release v11 work clock. **Full** adds the complete span sampler. **Profiled** loads the saved profile, checks the digest of the current executable and instruments only those scalar read instructions. The v11 meter, 5 ms polling interval, 100-billion-unit work budget, and 120 s outer timeout are common to all modes. Coverage is the number of eligible scalar reads recorded in Profiled divided by those recorded in its matched Full run. It does not count every memory access. [Collector](../experiments/first_run_sampler/bench.py) · [Original sampler](GCE_CPP20_LOCALITY.md)

The main cost measure sums child user and system CPU from `wait4` and the polling parent's CPU over the operational interval. It includes hashing the ELF in every mode, loading and validating a cached profile, and constructing and writing the first one. The parent closes the buffered file and excludes subsequent research verification and export from this interval. First use means no existing profile. Guest integrity checks have already read the executables. A second calculation subtracts the measured hash CPU from Live and Full to estimate how costs change when those baselines can skip the preflight. [Cost and coverage criteria](../experiments/first_run_sampler/design.json)

The next study measures omitted eligible reads during the same execution and reruns a low-coverage input under Full. It tests both unseen loops and shifts in frequency between two previously observed instructions. [Coverage checks and measured recovery costs](GCE_GUARDED_SAMPLER.md)

## Replay and reproduction

Python 3.11 or later can verify every placement trace, process record, terminal clock, first-run profile, held-out comparison and reported cost from the published captures. The optional archive and manifest arguments check the original five executable identities, inputs and expected outputs against the Aonohako corpus. [Original Aonohako corpus](https://github.com/seo-rii/aonohako/tree/b938afaa6c171a8a56e24d376d6662f93fcea4c6/docs/durante/gce-v11-cpp20-accuracy/corpus)

```sh
study=experiments/first_run_sampler
data=experiments/results/gce-first-run-sampler
python3 "$study/verify.py" "$data" --elf-archive "$corpus/elfs.tar.xz" --upstream-cases "$corpus/cases.json"
python3 "$study/score.py" "$data/dataset.json.gz" "$data/receipts.tar.xz" --verify "$data/evaluation.json"
python3 -m unittest discover -s tests -p 'test_first_run*.py'
```

To prepare a new measurement, use the v11 release installation, full span client and profiled client built as in the previous experiments. The materializer checks each file against the preregistered digest. Launch the resulting payload on a disposable Milan guest configured with one thread per core and two exposed CPUs. The trace controller runs on CPU 0. The systemd cpuset confines the parent and its children to CPU 1. [Runtime and full client build](GCE_CPP20_LOCALITY.md#reproducing-the-measurements) · [Profiled client build](GCE_LIVE_SAMPLER_FAST.md#offline-verification-and-use)

```sh
python3 "$study/prepare.py" "$tmp/payload" --release "$release" --elf-archive "$corpus/elfs.tar.xz" \
  --span "$span_build/libaccess_span.so" --profiled "$fast_build/libsampler_profiled.so"
# From the materialized payload on the guest
sudo taskset -c 0 ./guest.sh
```

For a different trusted single-threaded ELF, the two helper commands below use the full client's schema-2 receipt and the observed PID of the calibration launch. Resolution rehashes the executable and returns decimal module offsets for the profiled client's `-sites` option. The reported stdin and sampler mode must come from the same trusted invocation. The full and profiled clients share a receipt format. Retain the original receipt to verify its identity and recompute the selected sites. [Profile helper](../experiments/first_run_sampler/profile.py)

```sh
python3 "$study/profile.py" capture --arm full --elf "$elf" --stdin "$input" \
  --receipt "$run/sample.json" --pid "$pid" --out "$profile"
python3 "$study/profile.py" resolve --elf "$elf" --profile "$profile"
```
