# Capacity-miss time features

Design, harness and results of the study in
[docs/CAPACITY_MISSES.md](../../docs/CAPACITY_MISSES.md).

- `predeclared.txt`, `plan.json`, `analyze.py`: round 1 (14 kernels, 22 held-out programs).
- `predeclared-v2.txt`, `plan-v2.json`, `analyze_v2.py`: round 2 (round-1 cases as
  calibration, 49 contest variants held out) and the verdict analysis of
  `docs/JUDGE_DISAGREEMENT.md`.
- `kernels.c`: calibration kernels. `collect.py`: builds, checks outputs, runs the
  cache probe and times native runs.
- `results/v1`, `results/v2`: the recorded runs.
