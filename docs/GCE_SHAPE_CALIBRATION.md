# Choosing a calibration for an input

## What the selector measures

Each input has a completed profile of its eligible scalar loads. Sites are identified by their instruction offset within the exact static executable. The spatial comparison uses the share of loads per site, the fraction of consecutive sampled pairs farther than one cache line apart, and the sampled address span. The temporal sampler starts a window every 8,191 visits to each site. Over the next 32 visits it counts whether the first line occurs again, and whether any reference comes within 64 lines of it. Both samplers keep the underlying addresses inside the measured process. [Registered rules](../experiments/shape_calibration/rules.json) · [Temporal sampler source](../experiments/shape_calibration/probe/temporal.patch)

The selector tries earlier native calibrations of the same ELF in the supplied order. A site is material if it accounts for at least 5% of eligible loads in either input. The spatial rule requires enough observed pairs and material coverage. The temporal rule additionally requires at least 64 completed windows per material site in each capture and limits changes in each recurrence fraction to 1/8. If no prior qualifies, the selector takes one direct native CPU measurement on the new input, checks its output, and saves a policy for later inputs. Both methods scale the selected native time with the positive instruction and REP weights from the [original clock](GCE_GUARDED_NATIVE.md). [Selector](../experiments/shape_calibration/select_policy.py)

This is a diagnostic for trusted, deterministic, single-process static Linux x86-64 executables. To classify an unfamiliar input it first runs the program to completion under the selected samplers. Qualification work, the subsequent live execution, and a fresh native run on a profile mismatch have separate costs.

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
