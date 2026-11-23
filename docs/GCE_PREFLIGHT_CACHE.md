# Exact-input preflight reuse

A private Durante session can reuse a complete input profile and its selected clock on a later request. Each deadline still starts a fresh target with the production v11 meter and reads that target's own live or terminal counters. A first request on a different input must still pay for its preflight.

Use the session for operator-owned, immutable, single-process static x86-64 programs whose observable behavior depends on the executable and stdin. Keep the runtime, probes and calibration bank under the operator's control. Each paid calibration belongs to a single boot and pinned CPU. The cache identity also covers the selector method, registered tools, implementation, invocation context, executable, input and ordered paid reference entries. A different reference bank can therefore miss even on a previously seen input. Cache hits check the original evidence. An execution that finishes with different stdout from the preflight yields an unavailable diagnostic. An early clock stop can have only partial output.

## Pinned guest results

Four input requests each used the same paid reference bank in two arms. The control arm always captured a new profile. The session arm missed on each first request and hit on its next two repetitions. Each preparation was followed by two actual, separately launched deadline runs, for 48 enforced runs in total. The four inputs included two operation counts for the same Fenwick-tree ELF. Both inputs incurred a cold miss against that bank. The [schedule](../experiments/preflight_cache/plan.json) was fixed before measurement.

| Average per request | Always profile | Session | Session change |
|---|---:|---:|---:|
| First preflight CPU, 4 requests | 489.76 ms | 492.23 ms | +0.5% |
| Repeated preflight CPU, 8 requests | 503.02 ms | 20.31 ms | −96.0% |
| Repeated preflight plus both live runs, CPU | 1,095.86 ms | 602.18 ms | −45.0% |
| Repeated preflight plus both live runs, elapsed | 1,098.89 ms | 602.47 ms | −45.2% |

The repeated session preflights launched zero targets. Their remaining CPU cost includes reading and checking the registered tools, bank, inputs and cached evidence. All 12 matched pairs selected identical paid policies. The 24 paired deadline decisions agreed. Aonohako's Go CLI independently replayed all 48 observed outcomes, including 24 timeouts. These measurements cover repeated use of the same input and bank on three trusted static C++ programs. Rejudging an identical executable and testcase can benefit. A stream of new executables or different testcases pays the first-use cost. The comparison reused existing native calibrations in both arms and did not collect new native accuracy references. [Metrics](../experiments/results/gce-preflight-cache/metrics.json) · [Raw observations](../experiments/results/gce-preflight-cache/output.tar.xz)

Three initial paid references cost 1.640 s of controller plus child CPU in total, charged separately. The session used 843,402 logical bytes for three seed entries, four query entries and session metadata. The study uses an already running Python controller and includes reading and verifying preparation evidence and both actual enforce/reap operations. The timing intervals exclude interpreter startup. The kernel trace verified 70 measured PIDs on CPU 1 of an AMD Milan GCE VM with one thread per core. Steal ticks and lost trace events were zero. The VM and disk returned HTTP 404 after cleanup and the temporary SSH key was removed. [Guest receipt and hashes](../experiments/results/gce-preflight-cache/collection.json)

### Reproduce the measurements and Go comparison

From the Durante checkout, extract the raw evidence to an empty directory. Recompute the metrics. For the cross-project check, use the three registered ELFs and Aonohako's `durante-reference` built with the [input-deadline command](https://github.com/seo-rii/aonohako/pull/155).

```sh
results=experiments/results/gce-preflight-cache
scratch=$(mktemp -d)
tar -xJf "$results/output.tar.xz" -C "$scratch"
python3 -B experiments/preflight_cache/verify.py "$scratch/output" --output "$scratch/metrics.json"
cmp "$scratch/metrics.json" "$results/metrics.json"
python3 -B experiments/preflight_cache/replay_aonohako.py "$scratch/output" \
  --binaries "$binaries" --reference "$aonohako_reference"
```

To assemble a new guest payload, use `experiments/calibrated_limits/prepare.py` with `--study experiments/preflight_cache --source-dir experiments/shape_calibration`, the registered `--runtime`, the registered `--probe` and a new output directory.

## Run a trusted input

The following example starts a private cache and enforces two different deadlines on the same ELF and input. Use a CPU in the caller's allowed set. The commands use the registered runtime and temporal probe from the [combined sampler setup](GCE_SHAPE_CALIBRATION.md).

```sh
session=$(mktemp -d)
for ms in 75 300; do
  taskset -c "$cpu" python3 -B experiments/preflight_cache/session.py \
    --payload "$runtime" --probe "$probe" --elf "$binary" --stdin "$input" \
    --cache "$session/cache" --limit-ms "$ms" --out "$session/limit-$ms"
  rc=$?
  case "$rc" in 0|123) ;; *) exit "$rc" ;; esac
done
```

Inspect each `cache-use.json` for the hit flag and each `result.json` for the fresh execution's PID and decision. The CLI returns 0 within budget, 123 when the clock exceeds the limit, 124 on the external watchdog and 1 when evidence is unavailable. To select among earlier paid calibrations for another input, pass their capture directories with repeated `--reference` in oldest-first order and retain that ordering for later lookups. Only captures that paid for their own native calibration can be references. Create a new private session directory when switching hosts or reference environments. The operator controls cache lifetime and storage. Start another session to refresh native calibrations after a change in host performance. Python callers using the lower-level prepare function must perform cold captures on the main thread because the sampler installs a signal handler. Concurrent CLI processes can share the directory.
