# Choosing a calibration for an input

On ten held-out C++ inputs a single calibration per ELF missed four native timeouts. Comparing spatial load profiles before reusing a calibration reduced the misses to two. Comparing both spatial and temporal load profiles reduced them to zero. With the selected calibration, all **40 actual deadline decisions** agreed with the nine native runs per input. The live clock killed 20 jobs and let 20 finish. The selected policy required an extra native run for three held-out inputs and reused an earlier calibration for seven. [Timing, costs and decisions](../experiments/results/gce-shape-calibration/metrics.json) · [Guest receipts](../experiments/results/gce-shape-calibration/output.tar.xz)

| Method | Earlier four inputs within ±20% | Six new inputs within ±20% | Missed native deadlines, out of 40 | New native calibrations on held-out inputs |
|---|---:|---:|---:|---:|
| First calibration for each ELF | 2/4 | 3/6 | 4 | 0 |
| Compare spatial profiles | 3/4 | 4/6 | 2 | 2 |
| Compare spatial and temporal profiles | 4/4 | 5/6 | 0 | 3 |

The binary searches in `lookup/train` and `lookup/shuffled` executed the same main load site about 24 million times. In the ordered case the sampled line was read again within 32 visits in 2,928 of 2,931 windows (99.90%). With shuffled queries that fell to 1,222 of 2,931 (41.69%). Their spatial profiles passed the reuse rule. The temporal comparison requested a new native anchor. The later, longer shuffled input reused that anchor and was estimated at 272.45 ms against a median of 272.04 ms. [Per-input profile comparisons](../experiments/results/gce-shape-calibration/output.tar.xz)

`fenwick/expanded` passed the reuse check against an earlier large tree and remained **22.30% too low** (162.03 ms estimated, 208.52 ms native median, 203.14–242.65 ms across the nine runs). The registered 150 ms and 300 ms limits both gave the right decision. A 175 ms limit would have exposed a missed timeout on this input. That boundary is a retrospective example. The experiment used three static executables on AMD Milan and has not qualified contestant grading.

## What the selector measures

Each input has a completed profile of its eligible scalar loads. Sites are identified by their instruction offset within the exact static executable. The spatial comparison uses the share of loads per site, the fraction of consecutive sampled pairs farther than one cache line apart, and the sampled address span. The temporal sampler starts a window every 8,191 visits to each site. Over the next 32 visits it counts whether the first line occurs again, and whether any reference comes within 64 lines of it. Both samplers keep the underlying addresses inside the measured process. [Registered rules](../experiments/shape_calibration/rules.json) · [Temporal sampler source](../experiments/shape_calibration/probe/temporal.patch)

The selector tries earlier native calibrations of the same ELF in the supplied order. A site is material if it accounts for at least 5% of eligible loads in either input. The spatial rule requires enough observed pairs and material coverage. The temporal rule additionally requires at least 64 completed windows per material site in each capture and limits changes in each recurrence fraction to 1/8. If no prior qualifies, the selector takes one direct native CPU measurement on the new input, checks its output, and saves a policy for later inputs. Both methods scale the selected native time with the positive instruction and REP weights from the [original clock](GCE_GUARDED_NATIVE.md). [Selector](../experiments/shape_calibration/select_policy.py)

This is a diagnostic for trusted, deterministic, single-process static Linux x86-64 executables. To classify an unfamiliar input it first runs the program to completion under the selected samplers. Qualification work, the subsequent live execution, and a fresh native run on a profile mismatch have separate costs.

## Profiling CPU and the one-pass implementation

The first guest collected the two profiles in separate runs so that their signals and costs could be checked independently. On the ten held-out inputs the full spatial captures used 3.446 s of target CPU, the temporal captures 3.477 s, and the three extra native calibrations 0.282 s. Their sum, 7.205 s, was 2.295 times the CPU used by ten complete plain v11 controls (3.140 s). The native median times summed to 1.283 s. These costs exclude the three initial calibrations, the controller and the later live-limit launches. Each sampler prices behavior on a completed input; cases that need just one run still pay this upfront cost.

A second fresh pinned Milan guest compared the separate captures against the new default, which loads the release meter and both samplers in one instrumented process. All three clients see the same target PID. The two address reports share a single instruction/REP clock, terminal v11 report and child CPU charge. Both samplers independently produce the same per-site eligible load counts in each combined run. The paired schedule alternated order, rotated the 13 inputs and repeated each input three times. [Follow-up registration](https://github.com/seo-rii/durante/commit/46be9cd) · [Combined capture implementation](../experiments/shape_calibration/combined.py)

| Paired inputs | Pairs | Separate target CPU | Combined target CPU | Reduction |
|---|---:|---:|---:|---:|
| Initial calibration inputs | 9 | 3.516 s | 2.017 s | 42.65% |
| Held-out inputs from the earlier study | 12 | 8.219 s | 4.451 s | 45.85% |
| Prospective held-out inputs | 18 | 12.435 s | 6.904 s | 44.48% |
| **All inputs** | **39** | **24.170 s** | **13.371 s** | **44.68%** |

Each of the 39 individual pairs used less target CPU with the combined sampler. The savings ranged from 38.39% to 49.70%. On the new guest all 39 pairs chose the same anchors as one another and as the first guest. All 120 cutoff comparisons agreed (40 distinct limits repeated three times) with the first guest's stored calibrations. The second guest measured sampling cost and decision reproducibility. The 40 live-limit outcomes and independent native references come from the first guest. Both guests recorded zero steal ticks and zero lost trace events. Their VM and boot disk returned HTTP 404 after deletion and their temporary SSH keys were removed. [Paired results](../experiments/results/gce-shape-combined/metrics.json) · [Paired raw receipts](../experiments/results/gce-shape-combined/output.tar.xz) · [First guest cleanup](../experiments/results/gce-shape-calibration/collection.json) · [Second guest cleanup](../experiments/results/gce-shape-combined/collection.json)

## Capturing and applying a policy

Use the registered [guarded v11 payload](GCE_GUARDED_SAMPLER.md#using-the-diagnostic-on-a-new-executable). The temporal probe is provided with the receipts. Its SHA-256 is checked before loading it. Keep each capture and its selection in a separate private directory. Run capture and native calibration on the intended worker CPU.

```sh
study=experiments/shape_calibration
data=experiments/results/gce-shape-calibration
scratch=$(mktemp -d)
xz -dc "$data/temporal.so.xz" > "$scratch/temporal.so"
probe_sha=$(sha256sum "$scratch/temporal.so" | cut -d' ' -f1)

taskset -c "$cpu" python3 "$study/capture.py" --payload "$payload" --probe "$scratch/temporal.so" \
  --elf "$elf" --stdin "$first_input" --out "$first"
taskset -c "$cpu" python3 "$study/select_policy.py" --elf "$elf" --run "$first" --method temporal \
  --probe-sha256 "$probe_sha" --out "$first/selection.json"

taskset -c "$cpu" python3 "$study/capture.py" --payload "$payload" --probe "$scratch/temporal.so" \
  --elf "$elf" --stdin "$next_input" --out "$next"
taskset -c "$cpu" python3 "$study/select_policy.py" --elf "$elf" --run "$next" --method temporal \
  --reference "$first" --probe-sha256 "$probe_sha" --out "$next/selection.json"
```

If the selection says `reuse`, choose `policy.json` from the zero-based `reference_index` in the supplied list of reference directories. If it says `calibrate`, choose `$next/policy.json`. Pass only previously calibrated inputs as references, oldest first. A later limit on these same input bytes can use the saved selection. The runner checks the ELF, the supplied input against the captured input, the selected policy and the metering receipts before it launches the target.

```sh
taskset -c "$cpu" python3 "$study/run.py" --payload "$payload" --elf "$elf" --stdin "$next_input" \
  --run "$next" --selection "$next/selection.json" --policy "$selected_policy" \
  --out "$new_run" --limit-ms 75 --timeout-ms 120000
```

Exit 0 is completion within the work limit, 123 is a live or terminal work-limit crossing, 124 is an outer wall timeout and 1 is an unavailable decision. The selection and some of the emitted work data are application mutable. The intended use is measurement of known executables. [Input-checked runner](../experiments/shape_calibration/run.py)

## Replaying the measurements

The thresholds, workload identities, cohorts and limits were published before guest collection. A new AMD Milan n2d-standard-4 guest ran the parent and targets on CPU 1 with one thread per core. CPU 0 recorded exec events. One initial native anchor was available for each of three ELFs. All later choices used previously paid anchors from the appropriate method. When both methods requested a new native observation for one input, they shared that job. The guest first made all selections and enforced the selected clock limits. It then collected nine independent native CPU times for each of the ten held-out inputs. [Registration](https://github.com/seo-rii/durante/commit/151a763) · [Case manifest](../experiments/shape_calibration/plan.json)

```sh
study=experiments/shape_calibration
data=experiments/results/gce-shape-calibration
scratch=$(mktemp -d)
tar -xJf "$data/output.tar.xz" -C "$scratch"
python3 "$study/verify.py" "$scratch/output" --output "$scratch/metrics.json"
cmp "$scratch/metrics.json" "$data/metrics.json"
```

To replay the paired CPU comparison, extract `experiments/results/gce-shape-combined/output.tar.xz` into a new directory and run `verify_combined.py` there with the extracted `output` directory as its argument. It also verifies the first capture against the reference archive SHA-256 in the comparison plan. To rebuild the sampler use CMake Release with DynamoRIO 11.3.0-1, the study's `probe` source directory and the SDK's `cmake` directory as `DynamoRIO_DIR`. The two guest payloads use `prepare.py OUT --runtime PAYLOAD --probe LIB` and the same command with `--combined-benchmark`. [Verification code](../experiments/shape_calibration/verify_combined.py)

The [large-footprint follow-up](GCE_FOOTPRINT_ADMISSION.md) tests a stricter reuse rule on nine prospective inputs and compares both clocks at 52 real deadlines.
