# Collect flat-cache and branch-predictor counters in one run

One DynamoRIO invocation can collect the inputs to the ML4 plus gshare diagnostic score. Across six literal-matching cases on two independent GCE AMD Milan boots, the joint collector reproduced every separate work, cache and predictor field. Running the submitted program once saved 4.65–12.28% of collector CPU time. Both criteria and the exact executables, inputs, clients and schedule were [registered before collection](https://github.com/seo-rii/durante/commit/f31495731feefad5346da7df327cba2d997f6657).

| Text and solver | CPU saved, primary | CPU saved, repeat |
| --- | ---: | ---: |
| Random, KMP | 11.49% | 10.81% |
| Random, Z | 10.00% | 10.06% |
| Random, naive | 12.28% | 11.58% |
| Periodic, KMP | 10.51% | 10.51% |
| Periodic, Z | 9.39% | 9.54% |
| Periodic, naive | 4.71% | 4.65% |

Each measurement includes process startup and child user plus system CPU. The baseline is the sum of the flat-cache median and the separate gshare collection median for that case on that boot. Each boot ran every case three times per diagnostic configuration and five times natively, for 168 runs in total. The schedule was reversed on the second boot. Both used CPU 1, one thread per core and zero recorded steal ticks. The replay also compares the counters with the archived [separate matcher study](../string_matching/). Single-pass collection still took 89–238 times the native CPU on this panel. These results cover the registered repeatable single-process C++20 submissions. Identical model inputs preserve the existing score and its timing errors.

## Run the two clients together

With the registered guest payload unpacked, use the flat-cache client as the first client and the predictor as the second. From its root, the following shell commands run the random-text KMP case. Use an absolute working path without whitespace for the client option paths. The predictor writes JSON, the cache probe writes its metrics to standard error, and Durante writes work records to the supplied directory.

```sh
root=$(pwd -P)
slot=$(mktemp -d)
mkdir "$slot/records"
ulimit -s unlimited
taskset -c 1 setarch x86_64 -R env DYNAMORIO_CONFIGDIR="$slot" LC_ALL=C \
  "$root/dynamorio/bin64/drrun" -root "$root/dynamorio" -quiet \
  -client "$root/flat.so" 00000000 "-outdir $slot/records -cpuid_profile x86-64-v2 -rep_bytes -latency_bound -probe_l1d -probe_outer" \
  -client "$root/branch.so" 00000001 "-out $slot/predictor.json -bpred" \
  -follow_children -disable_traces -vm_size 32M -vmheap_size 32M \
  -- "$root/bin/random-kmp" < "$root/inputs/random" > "$slot/stdout" 2> "$slot/probe.txt"
```

The full experiment uses the launcher in [guest.py](guest.py), verifies each executable, client, input and expected output, and records every child's CPU use. To rebuild the guest archive from the registered string-matching payload, run `python3 -B prepare.py PATH_TO_MATCHER_ARCHIVE NEW_ARCHIVE NEW_REGISTRATION NEW_PLAN` and compare the generated registration with [registration.json](registration.json).

Run `python3 -B -m unittest` and `python3 -B replay.py --verify results.json` in this directory. The replay checks both new archives and their guest and disk teardown receipts, the two historical archives, the individual counters and the per-case CPU medians. [Results](results.json) · [Protocol](protocol.json) · [Guest receipts](evidence/cloud.json)
