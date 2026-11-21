# Predicting changes in Euclidean loop length on a pinned Compute Engine CPU

**The history rule passed all four new Rust GCD inputs and both repeat inputs on an independent Milan VM.** On the previously failing alternating input, the earlier model estimated 152.884 ms, the new rule estimated 71.622 ms and native CPU was 71.093 ms. All four estimates for each input were within 5.61% of its native median. The extra instrumented CPU was 4.37% across 24 pairs. The validation covers the identified 32-bit Euclidean loops and the guest configuration studied here. [Frozen predictions and CPU observations](../experiments/results/gce-gcd-history/evaluation.json) · [Raw records](../experiments/results/gce-gcd-history/receipts.tar.xz)

| Rust input | Native median | v9001 | Every change charged | With history | Error with history |
| --- | ---: | ---: | ---: | ---: | ---: |
| Repeat, random GCD seed B | 76.447 ms | 60.757 ms | 76.069 ms | 72.352 ms | −5.36% |
| Repeat, alternating lengths | 71.093 ms | 112.590 ms | 152.884 ms | 71.622 ms | +0.74% |
| New random GCD seed D | 113.570 ms | 91.114 ms | 114.094 ms | 108.537 ms | −4.43% |
| New runs of two (AABB) | 70.355 ms | 112.590 ms | 112.253 ms | 71.623 ms | +1.80% |
| New runs of 32 | 70.227 ms | 112.590 ms | 74.162 ms | 74.162 ms | +5.60% |
| New permuted input | 83.088 ms | 113.763 ms | 114.139 ms | 82.843 ms | −0.29% |

On the four new inputs, the baseline passed one, the flat surcharge passed two and the history model passed four. For strict alternation and AABB, the history model charged two cold changes each, compared with 2,097,151 and 1,048,575 total changes. For runs of 32 it charged all 65,535 changes. For the permuted input it charged 259,322 of 1,066,990 changes. The permuted workload uses a separate executable with additional index arithmetic, so its native difference includes that work. Both random GCD inputs became less accurate than the flat model after the history discount. Every one of the 24 site reports agreed with the independently generated input oracle. [Input counts](../experiments/results/gce-gcd-history/fixtures.json) · [Per-run signed errors](../experiments/results/gce-gcd-history/evaluation.json)

## What the counter measures

The earlier GCD study identified the hot closing branch in the executable and counted how often two adjacent traversals had different lengths. A flat cost per change reduced the timing error on three random inputs to within 1.3%, but it estimated 152.884 ms for a regularly alternating input that used 71.068 ms of native CPU. We retained its executable selector, signed per-division adjustment and per-change cost. The new client tests whether the next length can be predicted from its recent history. [Original estimator and counterexample](GCE_GCD_CONTROLS.md) · [Frozen weights](../experiments/gcd_holdout/parameters.json)

For each eligible branch, the probe remembers the last length and two tables of previously observed successors. One table uses the preceding length as the key and the other uses the preceding two lengths. Each has 64 slots and stores full keys, so a collision can evict a prediction but cannot turn a different key into a match. The current length is charged only if it changes and neither table predicts it. Both candidate lengths are computed from past observations. Either can match the current length. This measures whether history contained the observed outcome, without selecting a single prediction before execution. Table updates follow the comparison. The initial completion populates the branch history and incurs no change charge. The extra work runs in the existing loop-exit callback. The reported CPU overhead includes loading the probe, counting visits and running those callbacks. [Probe source](../experiments/gcd_history/probe) · [Estimator](../experiments/gcd_history/model.py)

Before timing any programs, an input-only replay found that the first proposed table index mapped every pair of equal odd lengths to slot 32. The repeated pairs (7,7) and (27,27) continually evicted one another. The published study uses the high bits of a multiplicative hash for two-length keys. The fixture also records the counterfactual miss count under the original index. Both rules keep exact tags. [Registered design](../experiments/gcd_history/evaluation-plan.json) · [Input oracles](../experiments/results/gce-gcd-history/fixtures.json)

## Inputs and timing

We published the protocol and implementation before collecting timings. [Acquisition freeze](https://github.com/seo-rii/durante/commit/b7dac1cf6fe1f4c53351f2a15f3205a0bf719dd9)

The third study repeats two previously measured inputs and introduces four new ones. The Fibonacci programs perform the same 35,651,584 divisions on the same multiset of scaled pairs. Their two possible chain lengths are 7 and 27. The inputs alternate the lengths, use runs of two or 32, or permute the pair ranks with a reversible integer transformation. The two random GCD inputs exercise a wider distribution of lengths. All counts in the report come from the measured executable; the fixture generator independently computes the expected counters. [Programs and acquisition protocol](../experiments/gcd_history) · [Archived executables and inputs](../experiments/results/gce-gcd-history/fixtures.json)

Each condition ran in four native-bracketed quartets. The two diagnostics, the fast v9001 meter alone and the same meter with the schema-2 probe, exchanged positions across rounds. All estimates are compared with the median of the eight native user-plus-system CPU times. A pass requires all four estimates to fall within ±20%.

All six cases met the duration and stability gates. The minimum native observation was 69.474 ms and the largest within-case spread was 4.26%. The pooled CPU times for the diagnostics were 27.199 s for version 9001 alone and 28.387 s with history; the median individual pair increase was 4.12%. The kernel trace located 96 measured processes and 48 same-PID loader executions on guest CPU 1, with no dropped events and zero guest steal ticks during the 59-second interval. [Platform and teardown confirmation](../experiments/results/gce-gcd-history/platform.json) · [Per-process CPU and placement](../experiments/results/gce-gcd-history)

These results support this opt-in estimator for the measured Euclidean patterns. Other algorithms and signed or wider divisions still need their own validation. In particular, binary search has a substantial error without a qualifying Euclidean loop. [Historical Rust holdouts](GCE_DIVIDE_CLOCK.md)

## Reproduction

Run the following from the Durante repository on Linux x86-64. The test regenerates source-level oracles, checks the archived kernel placement and all process receipts, and replays the score for every case.

```sh
python3 -m unittest discover -s tests -p 'test_gcd_history.py'
```

A DynamoRIO SDK and launcher also exercise the standalone client with hand-counted, interrupted and colliding histories. [Regression tests](../tests/test_gcd_history.py) · [Raw data and estimates](../experiments/results/gce-gcd-history)
