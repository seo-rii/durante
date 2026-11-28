# Removing the most expensive affordable item

For each budget, sell the single most expensive remaining item whose price is at most that budget. Report its price, or `-1` if no item fits. Duplicate prices represent distinct items. The suite compares an ordered multiset, a Fenwick frequency tree with rank selection, and a sorted array with disjoint sets that skip deleted positions. All three read the same input and print each individual answer. A Python oracle removes elements directly from a sorted list.

## Registered comparison

Two inputs contain 65,536 initial items and 73,728 purchases. Prices are between 1 and 131,072. Both inputs have identical initial prices and budgets as multisets. The uniform input preserves the seeded query order. The clustered input alternately sorts each successive block of 512 budgets in ascending and descending order. Reordering can change which sales succeed, so each input has its own full output oracle.

Each input uses its own disjoint-set solver as the reference. The instruction/REP model (M0) and the latency/cache model (ML4) use the fixed Dijkstra coefficients from the earlier Milan study. The clock for a submission is its model score divided by the corresponding reference model score. Native time is the submission CPU time divided by the reference CPU time. No predecessor observation participates in fitting the weights.

Each independent SMT-off Milan VM pins every measured execution to CPU 1. There are seven native and two full diagnostic runs per executable/input, or 54 runs per boot. A factor of 1.25, 1.5, 2 or 3 times the reference is an inclusive deadline. A comparison is determinate when every pair of native measurements gives the same decision and all diagnostic score pairs give the same decision. Report false accepts, false timeouts and indeterminate comparisons on each boot and when pooling both. Also report each median native ratio, signed relative timing error for both models and whether all four full-probe readings per program/input agree. These are simulated deadlines using completed observations.

`prepare.py` takes the registered corrected Milan payload, builds the solvers, checks each output and writes a deterministic guest archive and file manifest. The VM uses the previously published Milan collector and full cache probe unchanged.
