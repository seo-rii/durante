# Binary-search query order on a pinned Compute Engine CPU

Changing the order of 1,048,576 searches in the same 1 MiB table changed native CPU from 62.968 ms to 24.075 ms for a conditional-move loop. The two input orders produced identical outputs and identical v9001 instruction, latency and operand-byte totals. With an actual data-dependent branch, the corresponding times were 137.581 ms and 27.582 ms. A sparse endpoint sampler identified the change in order. Its timing correction transferred to the archived Rust input, but missed both other table sizes and a new Rust input. [Measurements](../experiments/results/gce-search-order/dataset.json.gz) · [Predictions and contrasts](../experiments/results/gce-search-order/evaluation.json)

## Matched search programs

The archived Rust executable selects its next index with an indexed CMP and CMOVB. Only the loop-control branch follows a conditional jump on the remaining search depth. Earlier measurements had 34.455 ms native CPU against a 21.66 ms estimate. Charging for large distances between addresses of successive inner comparisons overestimated this same search. [Previous sampler and exact search-loop replay](GCE_ACCESS_SAMPLE.md)

Two assembly programs reproduce the Rust halving loop using CMOVB or a conditional jump and MOV. Their even-valued keys, terminal comparison, number of results and checksum agree. We generated the query files in advance and reopened them read-only for every measured process. Each timed child mapped its query file, initialized the key table, performed the searches and wrote the checksum. The Rust executable generates its queries inside the timed process. The randomized, sorted and block-sorted orders use the same query multiset, with a common checksum of 137,400,320,388. [Assembly](../experiments/search_order/search.S) · [Executable and input identities](../experiments/results/gce-search-order/fixtures.json)

With randomly ordered queries, replacing the conditional move with a data-dependent branch added 74.61 ms of native CPU. With sorted queries it added 3.51 ms. The difference between the two order effects was 71.10 ms. For conditional moves alone, the closest observed random and sorted native times were still 31.71 ms apart. Any identical prediction for these two inputs incurs at least 39.35% error on one of them even at the most favorable endpoints. Timing includes the effects of hardware caching, speculation and prediction; no hardware event counters were collected. [Raw process records](../experiments/results/gce-search-order/receipts.tar.xz)

## Endpoint sampling and timing estimates

At a registered final comparison, the opt-in client samples the terminal element index on visits 1 and 2, then 1022 and 1023, continuing every 1021 visits. The common instruction path decrements a countdown. The sampled path compares the indices after dropping their four least significant bits, grouping 16 elements per table-relative region. The bit length of their XOR is zero for equal regions and grows as their high-order prefixes diverge. We fitted a signed correction to the existing clock using two orders at 1 MiB per implementation. A second registered model charged only the part of the width above nine bits. The Rust cases used the conditional-move calibration. [Probe](../experiments/search_order/probe) · [Premeasurement registration](https://github.com/seo-rii/durante/commit/906ce6aee3313a7319a96fb8fbc5b050512a6c29)

| Held-out input | Native median | v9001 | Full-width correction | Error |
| --- | ---: | ---: | ---: | ---: |
| CMOV, sort each block of 32 | 60.611 ms | 41.676 ms | 54.502 ms | −10.08% |
| Conditional branch, sort each block of 32 | 135.925 ms | 13.959 ms | 113.637 ms | −16.40% |
| CMOV, random, 16 KiB | 13.640 ms | 27.416 ms | 30.726 ms | +125.25% |
| CMOV, random, 8 MiB | 170.016 ms | 49.510 ms | 80.298 ms | −52.77% |
| Archived Rust input, 262,144 keys | 37.645 ms | 22.039 ms | 32.812 ms | −12.84% |
| New Rust input, 100,003 keys | 38.794 ms | 52.019 ms | 74.415 ms | +91.82% |

The full-width correction passed three of the six held-out inputs; the original clock passed zero. The nine-bit threshold passed only the archived Rust input. Across all conditional-move workloads including Rust, even fitting new coefficients to all observed times would require at least 53.82% worst error using full width, or 42.95% using the threshold, allowing favorable native endpoints separately for each prediction. These bounds apply to a common linear correction on top of the fixed baseline. [Model and feasibility calculation](../experiments/search_order) · [Per-input results](../experiments/results/gce-search-order/evaluation.json)

Across 40 pairs the two meters used 35.228 s and 35.067 s of child CPU, a pooled difference of −0.46%. The median paired change was +0.12%. All 40 sampled histograms exactly matched independent input reconstruction. The randomized 1 MiB CMOV training case and 8 MiB test case exceeded the 10% native spread gate (14.68% and 18.42%); the 16 KiB test case was under the 20 ms minimum. Every observation is included. The guest scheduler trace puts all 160 children and 80 same-PID loader executions on CPU 1, with no guest steal ticks during the 77-second collection. After export, the instance and disk both returned HTTP 404. [Placement and cleanup](../experiments/results/gce-search-order/platform.json)

Replay the study from the repository root on Linux x86-64.

```sh
python3 -m unittest discover -s tests -p 'test_search_order.py'
```
