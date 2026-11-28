# Measuring Java contest CPU time

The collector compiles the selected contest problem once per Java profile,
then records native, Durante and large-budget runs in each round. It uses the
standard corpus's JVM flags, full input generator, output oracle, supervisor
commands and CPU affinity. Supply an SDK containing the patched runtime
and its matching client. The original collector and aggregation formula
come from [the Java timing study](https://github.com/seo-rii/durante/blob/76b01e2f3fddf437e0e2bcbe6f3998c4c5dd85f5/experiments/java_timing/run.py).

```sh
study=experiments/java_timing
python3 -B "$study/run.py" --output results.json --rounds 2 --cpus 0,1 \
  --runner-bin target/release/durante --config main="$SDK,$CLIENT" \
  --lang java=main --lang java-cds=main --problem bfs_grid
python3 -B "$study/summarize.py" results.json --write summary.json --markdown
```

Native runs are shared by all configurations within one profile, problem
and round. The summarizer checks the problem set from the input hashes, the
configurations selected for each profile, and every zero-based round index
before computing CPU-time minima, medians or geometric means. A partial
checkpoint can be inspected in `results.json`. A summary requires one
successful, correctly answered run in each declared cell for every round.

## OpenJDK 21 run for #112

`results.json` holds every sample of the run behind the Java rows of
[docs/CONTEST_CORPUS.md](../../docs/CONTEST_CORPUS.md#results) and the timings
in [docs/JIT_LIFECYCLE.md](../../docs/JIT_LIFECYCLE.md#writes-to-a-large-code-cache).
`summary.json` and the tables below are `summarize.py`'s output for it, and
`tests/test_java_timing_evidence.py` checks that they still are.

This is a performance observation. It does not qualify Java for time-limit
verdicts: a JIT's instruction count depends on how fast it runs, and
[docs/JUDGE_DISAGREEMENT.md](../../docs/JUDGE_DISAGREEMENT.md) applies to Java
as to every language.

### What ran

Profiles, each divided by its own native runs:

- `java`: the corpus's default flags with `-XX:-UseCompressedClassPointers`
  (Aonohako's ordinary Java 21 profile);
- `java-cds`: the same flags with `-XX:CompressedClassSpaceSize=64m`, which keeps
  class data sharing (Aonohako's `java21_cds: 1`).

Configurations, one runtime and one client each (SHA-256 prefixes; the full
digests are in the provenance):

| Name | Runtime `lib64/release/libdynamorio.so` | Client `libdurante.so` |
|---|---|---|
| `main` | `71bede0d…`, main's patch | `052d0ef7…`, main |
| `runtime` | `fd694243…`, this branch's patch | `052d0ef7…`, main |
| `client` | `71bede0d…`, main's patch | `3301351b…`, this branch |
| `both` | `fd694243…`, this branch's patch | `3301351b…`, this branch |

- The two runtimes are the prepared SDKs whose manifests are
  [evidence/runtime-main.json](evidence/runtime-main.json) (code replay patch
  `aad0137a…`, as on main) and
  [evidence/runtime-branch.json](evidence/runtime-branch.json) (`3f4ba639…`,
  the digest in `src/isolation.rs`). The branch patch adds
  `-page_granular_writable_code` and this branch's other runtime changes.
- main's client is built from 35587a4b82bac1de677d4bd6b15b98f1dcb8a576 and the
  branch client from 3b3a96529485d666fa179ecf5311cb9ed58bc9f3, both with
  `cmake -S client -B build -DCMAKE_BUILD_TYPE=Release`.
- The runner is built from eebf682c1bce521ea114e7d3d728df462d9623f0 with
  `cargo build --release`.
- `java` ran all four configurations, which separates the runtime change from
  the client change; `java-cds` ran `main` and `both`.

Protocol:

- The seven contest problems at the `full` tier, with the inputs of
  `tests/contest/problems.py` (hashes in the provenance) and the sources and
  Java commands of `tests/contest_corpus.py`.
- OpenJDK 21.0.13, `javac --release 17`.
- Each round runs, for every problem and profile, the native command and then
  each configuration, in an order rotated by one each round. Each
  configuration runs as a plain `durante run` and as a `budget-large` run, with
  a work budget of four times the plain run's work units, which also runs the
  live clock.
- 5 rounds, 490 samples. Every sample exited 0 with the reference output, and
  every Durante sample reported a valid measurement.
- `taskset -c 0,1` for every run, on a 4-vCPU Intel Xeon guest at 2.10 GHz,
  kernel 6.18.44, with nothing else running.
- CPU time is the whole tree's user plus system time from `wait4`. The
  recorded `maxrss_kb` is not reported: every child's `ru_maxrss` already
  includes the harness's RSS at fork, so it is at least 127 MiB even for
  native runs.

The samples were collected at 52c6d5da706478ad721e7303fb36d28ac5f5b7dd, a
local commit that was rewritten before it was pushed. Its `run.py` differs from
the one here (main's) only in argument checks and in two extra top-level fields
(`problems`, `complete`); the corpus and problem files are unchanged (their
hashes are in the provenance).

```
python3 -B experiments/java_timing/run.py --output results.json --rounds 5 --cpus 0,1 \
    --runner-bin target/release/durante \
    --config main=SDK_MAIN,CLIENT_MAIN --config runtime=SDK_BRANCH,CLIENT_MAIN \
    --config client=SDK_MAIN,CLIENT_BRANCH --config both=SDK_BRANCH,CLIENT_BRANCH \
    --lang java=main,runtime,client,both --lang java-cds=main,both
python3 -B experiments/java_timing/summarize.py experiments/java_timing/results.json \
    --write experiments/java_timing/summary.json --markdown
```

The tables use the minimum over the rounds, divided by the minimum of the same
profile's native runs, as the corpus does. `summary.json` also has the medians.

### Per problem

#### `java`, minimum of 5 rounds (x native)

| Problem | native (s) | main plain | main budget | runtime plain | runtime budget | client plain | client budget | both plain | both budget |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| bfs_grid | 0.315 | 26.7 | 28.4 | 12.9 | 12.6 | 27.5 | 28.8 | 12.5 | 13.4 |
| deep_dfs | 0.486 | 19.5 | 19.9 | 7.9 | 8.2 | 18.7 | 20.0 | 6.2 | 8.3 |
| dijkstra | 0.286 | 32.6 | 33.7 | 13.3 | 14.4 | 33.8 | 31.1 | 10.4 | 12.9 |
| fenwick | 0.386 | 22.1 | 23.5 | 11.4 | 11.5 | 18.3 | 24.8 | 8.8 | 11.2 |
| kmp | 0.723 | 13.2 | 12.9 | 5.7 | 6.0 | 12.9 | 13.8 | 5.3 | 5.9 |
| lcs | 0.340 | 15.5 | 17.9 | 8.8 | 9.0 | 15.6 | 18.7 | 8.0 | 8.1 |
| sort_bs | 0.518 | 15.9 | 18.2 | 9.2 | 8.4 | 18.1 | 18.1 | 8.2 | 8.9 |
| geometric mean |  | **19.9** | **21.1** | **9.5** | **9.7** | **19.7** | **21.4** | **8.2** | **9.5** |

Geometric means of `java` CPU seconds (minimum / median of rounds):

| Configuration | plain | budget-large |
|---|---:|---:|
| main | 8.26 / 9.17 | 8.78 / 9.88 |
| runtime | 3.97 / 4.26 | 4.01 / 4.46 |
| client | 8.20 / 9.03 | 8.92 / 9.69 |
| both | 3.39 / 3.78 | 3.94 / 4.38 |

#### `java-cds`, minimum of 5 rounds (x native)

| Problem | native (s) | main plain | main budget | both plain | both budget |
|---|---:|---:|---:|---:|---:|
| bfs_grid | 0.323 | 25.1 | 27.5 | 11.3 | 12.4 |
| deep_dfs | 0.412 | 19.8 | 21.8 | 6.7 | 8.9 |
| dijkstra | 0.237 | 33.4 | 34.3 | 11.4 | 15.5 |
| fenwick | 0.308 | 25.3 | 28.0 | 10.2 | 13.0 |
| kmp | 0.671 | 10.9 | 12.2 | 4.8 | 6.1 |
| lcs | 0.312 | 13.3 | 16.6 | 8.5 | 9.1 |
| sort_bs | 0.464 | 17.1 | 19.0 | 7.1 | 9.2 |
| geometric mean |  | **19.4** | **21.6** | **8.2** | **10.2** |

Geometric means of `java-cds` CPU seconds (minimum / median of rounds):

| Configuration | plain | budget-large |
|---|---:|---:|
| main | 7.18 / 7.86 | 8.00 / 8.78 |
| both | 3.05 / 3.30 | 3.77 / 4.12 |

### What each change contributes

Geometric means over the seven problems, CPU seconds (minimum of 5 rounds), and the
ratio of each change with the other part held fixed:

| `java` | plain | budget-large |
|---|---:|---:|
| main | 8.26 s (19.9x) | 8.78 s (21.1x) |
| runtime | 3.97 s (9.5x) | 4.01 s (9.7x) |
| client | 8.20 s (19.7x) | 8.92 s (21.4x) |
| both | 3.39 s (8.2x) | 3.94 s (9.5x) |
| runtime change, main client (runtime / main) | 0.480 | 0.457 |
| runtime change, new client (both / client) | 0.414 | 0.442 |
| client change, main runtime (client / main) | 0.993 | 1.015 |
| client change, new runtime (both / runtime) | 0.856 | 0.983 |

| `java-cds` | plain | budget-large |
|---|---:|---:|
| main | 7.18 s (19.4x) | 8.00 s (21.6x) |
| both | 3.05 s (8.2x) | 3.77 s (10.2x) |
| both / main | 0.424 | 0.471 |

Medians of the rounds instead of minima:

| | plain | budget-large |
|---|---:|---:|
| `java` main | 9.17 s (19.9x) | 9.88 s (21.5x) |
| `java` runtime | 4.26 s (9.3x) | 4.46 s (9.7x) |
| `java` client | 9.03 s (19.6x) | 9.69 s (21.1x) |
| `java` both | 3.78 s (8.2x) | 4.38 s (9.5x) |
| `java-cds` main | 7.86 s (19.4x) | 8.78 s (21.6x) |
| `java-cds` both | 3.30 s (8.1x) | 4.12 s (10.2x) |

Native geometric means: `java` 0.416 s, `java-cds` 0.370 s.

- Almost all of the gain comes from the runtime change: with either client it
  cuts the `java` time to 0.41-0.48 of what it was.
- The client change does nothing measurable on main's runtime (0.99 plain,
  1.02 budget-large). There the runtime's flushing and rebuilding of the code
  cache dominates: it was 75% of a hello world's profile
  ([docs/JIT_LIFECYCLE.md](../../docs/JIT_LIFECYCLE.md#writes-to-a-large-code-cache)).
- On the branch runtime the client change cuts plain runs by 14%. Budget-large
  runs, which use the live clock, change by under 2% (0.98).

### Counts of the same configurations

Two checks of the branch client and runtime against main's are in
[evidence/](evidence/).

- [evidence/count-parity.json](evidence/count-parity.json) (`parity.py`): the
  deterministic corpus cells under the same four configurations, each copied to
  one path first.
  - Static and dynamic C++: identical instruction and REP counts in all four
    configurations, in plain, budget-large and latency-bound runs (126
    comparisons).
  - Rust: identical across the runtime change. With the branch client it
    differs by −108 to +216 instructions, and by up to −5,169 with
    `--latency-bound`.
  - Rust's start-up parses `/proc/self/maps` (glibc's `pthread_getattr_np`),
    which lists where the runtime put the client. A location profile of
    `bfs_grid` with the callback-based `tests/count_locations` build (which
    stores no packed checkpoints) puts the whole difference, 84 instructions,
    in glibc's `strtoul`, parsing those addresses' hex digits.
  - With `--latency-bound` the program sees 139 map lines under the branch
    client and 141 under main's.
- [evidence/checkpoint-identity.json](evidence/checkpoint-identity.json)
  (`tests/checkpoint_identity.py`): the branch client's test build, which
  checks every settled packed write checkpoint against the full address of its
  write.
  - Covers Java 21 (both profiles), Node, PyPy and PHP's tracing JIT on the
    seven problems, plain and with the live clock, plus `tests/jit_stress.py`,
    `tests/jit_replay.py` and a fixture that writes its own code.
  - 75 cases and 2,931,241 settled words, with no difference.
