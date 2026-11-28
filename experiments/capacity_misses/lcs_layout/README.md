# Longest common subsequence under three memory layouts

Adding 7 ms to both absolute scores increased the median relative-time error on a fresh longest-common-subsequence problem. It introduced four false accepts on each pinned Milan boot. The original score matched every determinate native deadline on each boot. [Frozen model](https://github.com/seo-rii/durante/blob/13384362f13c824587b8359ed747a6f3f57f47b8/experiments/capacity_misses/score_offset/protocol.json) · [Prospective registration](https://github.com/seo-rii/durante/blob/f11d7d8000ba81f10dad2283a25d432cd3ca516a/experiments/capacity_misses/lcs_layout/protocol.json)

The two input strings are 1,152 and 2,304 bytes long. Reversing their order transposes the DP matrix, preserves its 2,654,208 inner cells and preserves the answer. A Python bit-parallel LCS oracle generated the expected length independently of the compiled dynamic programs. The intended solution keeps two rows of integers. The alternatives keep the full table in row-major or column-major storage. They visit the same logical cells in row order. Their timed processes include initialization and allocation. [Solvers](lcs.cpp) · [Inputs and oracle](inputs.py) · [Registered executable hashes](frozen.json)

The clock is the original ML4 score in ns plus 6.92658480142496 ns per simulated gshare miss. Both the original model and the candidate divide the submission score by its own input's rolling-row reference. The candidate adds exactly 7,000,000 ns to each before division. The five tested deadlines were 1, 1.25, 1.5, 2 and 3 times the reference. A native cell accepts if every submission/reference pairing meets the inclusive limit and times out if all exceed it. Other cells are mixed. [Exact case-level outcomes](results.json)

| Shape and alternative | Pooled native median ratio | Original model | With 7 ms |
| --- | ---: | ---: | ---: |
| wide-row | 1.488× | 1.721× | 1.303× |
| wide-column | 1.872× | 1.519× | 1.218× |
| tall-row | 1.508× | 1.714× | 1.301× |
| tall-column | 1.984× | 1.513× | 1.216× |

On each boot, 16 of 20 native cells were determinate. All four new false accepts were for the column-major table at 1.25× and 1.5×. Its reference score was small enough that the offset moved its ratio close to 1. The median absolute relative error across the four solver/input pairs went from 17.05% to 25.02% on the first boot, and from 17.26% to 23.12% on the repeat. The candidate failed both parts of the registered transfer criterion. The two shapes share inputs and code, so these are four comparisons on one problem.

Each of two independent AMD Milan guests used one hardware thread per core and CPU 1. Every executable/input pair ran seven times natively and twice in each of three diagnostic modes. The repeat reversed the schedule. Both guests reported zero steal ticks; their instances and disks returned 404 after deletion and both temporary keys were removed. [Raw archives and lifecycle receipts](evidence/)

From this directory run `python3 -B -m unittest` and `python3 -B replay.py --verify results.json` with the prior studies' Python/NumPy environment. To reproduce the payload from the registered range-distinct tools, run `python3 -B prepare.py --registered-tools PATH --output NEW_DIR` with the recorded compiler. The replay verifies both archives, manifests, CPU pinning, output hashes, diagnostic parity and all model decisions.
