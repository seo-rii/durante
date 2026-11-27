# Contest corpus

`tests/contest_corpus.py` runs contest-style submissions the way a judge would:
real problems with large inputs, in the languages and with the runtime flags a
judge uses, natively and under every `durante run` mode.

## Problems and languages

`tests/contest/problems.py` generates each input deterministically and solves
it with an independent reference solver.

| Problem | Work |
| --- | --- |
| `bfs_grid` | BFS on a 2000x2000 grid |
| `dijkstra` | Dijkstra with a binary heap, 200,000 nodes and 600,000 edges |
| `lcs` | Longest common subsequence DP, 8000x8000 |
| `fenwick` | 700,000 point updates and range sums |
| `kmp` | KMP over a 20 MB text |
| `sort_bs` | Sort and 500,000 binary searches, with heavy I/O |
| `deep_dfs` | Recursion to depth 1,000,000 on a path graph |

Each problem has a submission in C++ (`g++` static and dynamic, `clang++`),
Rust, Go, Java, JavaScript (Node), Python (CPython and PyPy), Ruby, Perl, PHP
(with and without the tracing JIT) and Lua. Java and Node use the flags of a
judge (aonohako's, with more heap). Interpreters get a smaller "lite" input so
that every cell finishes in seconds. `deep_dfs` recurses on a thread with a
512 MiB stack where the runtime needs one (Java, PyPy, Rust, Ruby). Node uses
`--stack-size`, and Lua uses an explicit stack. Every run has an unlimited
`RLIMIT_STACK`, as aonohako sets the hard limit.

## Modes and checks

| Mode | Requirement |
| --- | --- |
| `native` | Exit 0 with the reference output |
| `durante` | Complete, valid measurement and the reference output; repeated 3 times for native code |
| `budget-large` | `--work-budget` at 4x the measured work: `within_budget`, observed at `terminal`, receipt equal to the measurement |
| `budget-half` | Half the measured work: stopped live (exit 123, `work_budget_exceeded`) with a receipt above the limit and no complete measurement |
| `latency` | `--latency-bound`: complete and valid, bound at least 4x the instructions |

Repeated counts must be equal for static C++, and with `--no-aslr` also for
dynamically linked C++ and Rust: ASLR moves dynamically linked code by a few
instructions. The count with `--latency-bound` must equal the plain count for
the same code, except for Rust. Its start-up parses `/proc/self/maps`, which
also lists the runtime's mappings, and the larger instrumented blocks take a
few more code-cache units (see LATENCY_BOUND.md).

CI runs `bfs_grid` and `deep_dfs` in seven languages with `--no-aslr`.

```sh
python3 tests/contest_corpus.py --sdk toolkit/sdk/DynamoRIO-Linux-11.3.0-1 \
  --client build/libdurante.so --runner-bin target/release/durante \
  --output-dir NEW_DIR [--problem P ...] [--lang L ...] [--no-aslr]
```

## Results

The full matrix (7 problems x 14 languages x 5 modes, 546 runs) ran on two
CPUs of a Linux x86-64 guest with GCC 13.3, Clang 18, Rust 1.97, OpenJDK 21
(`--release 17`), Node 22, CPython 3.13, PyPy 7.3 and PHP 8.3. Every run gave
the reference output, the expected state, a valid measurement and a correct
receipt. Two kinds of count differences were flagged. With ASLR on, repeats of
dynamically linked code differed: by 2 instructions for C++, and by up to
about 3,000 for Rust. These disappear with `--no-aslr`. Rust's count also
differed with `--latency-bound`. The budget-half runs stopped at a median
0.52-0.67 of the full run's wall time.

CPU time of the whole tree relative to native, geometric mean over the seven
problems (minimum of repeats):

| Language | `durante` | `budget-large` |
| --- | --- | --- |
| C++ (g++ static) | 2.4x | 2.6x |
| C++ (g++ dynamic) | 2.7x | 3.1x |
| C++ (clang++) | 2.7x | 2.8x |
| Rust | 2.2x | 2.6x |
| Go | 3.2x | 3.3x |
| Java | 17.1x | 18.2x |
| Node | 7.9x | 8.1x |
| PyPy | 3.7x | 3.7x |
| CPython | 4.5x | 5.0x |
| Ruby | 6.0x | 6.1x |
| Perl | 5.3x | 5.7x |
| PHP | 6.0x | 6.0x |
| PHP with JIT | 6.3x | 5.9x |
| Lua | 4.3x | 4.5x |

Java's figure is mostly JVM start-up under the runtime, about 9 s per run.

The corpus showed two problems, both fixed with it:

- **Deep recursion crashed the runtime.** A recursion that overflowed a stack
  shared by its SIGSEGV handler ended in an internal runtime crash. Node
  without `--disable-wasm-trap-handler` reaches this, since it then has such a
  handler. The runtime patch now delivers SIGSEGV as the kernel does
  (SIGNAL_STATE.md).
- **`--latency-bound` was 5-34x slower than a plain run.** It charged the bound
  through a callback at every block and write. The bound is now charged inline,
  and the CI subset costs 1.05-1.52x a plain run with it: Node `deep_dfs`
  takes 23.7 s of CPU instead of 586.9 s, and CPython `bfs_grid` 4.7 s instead
  of 113.0 s.

The counts of JIT runtimes depend on how fast they run: Java `bfs_grid` counted
1.80e9 instructions in a plain run, 1.51e9 under a budget and 1.21e9 with the
old latency callbacks. Every measurement was still valid.
