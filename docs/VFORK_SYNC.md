# Cache reset during shared-memory child creation

A thread blocked in Linux `CLONE_VM | CLONE_VFORK` cannot acknowledge the
runtime's suspension signal until its child exits or executes another image.
A cache reset on another thread used to wait for that acknowledgment while
holding the runtime's thread-lifetime lock. The child could need the same lock,
or another thread stopped by that reset, to make progress.

The pinned runtime now publishes the parent's active vfork state under that
lock before entering the syscall. Synchronization requests with the existing
abort-on-failure policy stop before suspending such a parent and resume any
threads already suspended. They finish using the protected thread metadata
before releasing the locks. The parent clears its publication after returning
from the syscall, including a kernel error. Successful synchronization and the
other synchronization policies keep their existing contracts.

A rejected shared-memory clone also rolls back its reserved child stack and the
two initialization reservations that a real child would otherwise consume. The
parent only clears its saved handle after success: the child may have already
freed that stack. Each failed attempt removes only its own reservations; other
concurrent creations and shared signal-handler references remain intact.

## Reproducing the dependency

```sh
python3 tests/vfork_sync.py \
  --sdk /prepared/sdk --client /prepared/libdurante.so \
  --runner-bin /prepared/durante --output-dir /private/fresh-vfork-sync
```

This Linux x86-64 test requires native `clone3` support. It runs native controls,
then release and debug runtime profiles with the engine alone, the REP-off
client, and the production supervisor. Each child is held behind a pipe gate
while another parent thread requests one cache reset through the pinned runtime's
nudge format. A separate initialized worker precedes the vfork parent in the
runtime's thread-table walk, so synchronization attempts it before encountering
the blocked parent. Its exact synchronization state remains a runtime decision;
the test does not report an internal suspension count. Non-ignorable, mask-neutral
`rt_sigprocmask` calls force dispatcher entries after the nudge. The reset must
return and that worker must exit before the child is allowed to continue.

Both `clone` and `clone3` cover normal child exit, successful exec, failed exec,
and a kernel-rejected clone followed by a successful clone on the same parent
thread. Native signal handling verifies the test nudge itself. Instrumented
execution requires the runtime to consume it, exact application output, empty
stderr apart from the classified debug startup notice below, bounded completion,
and complete descendant cleanup. Measured modes also
check the process/image topology and compare the Rust aggregate with the Python
reference. No child or failed observation is retried or accepted as an expected
failure. `plan.json` and `results.json` retain input hashes and every row.

For these fixed `clone`/`clone3` cases, the debug profile may report exactly
`<Unknown processor cache size>` followed by a newline. The harness records it as
`cpu_cache_size_unknown`; successful `exec` may report exactly two such lines,
classified as `cpu_cache_size_unknown_after_exec`. This exception applies only
to the three instrumented modes. Native and release runs still require empty
stderr. Any other diagnostic, changed whitespace or excessive repetition fails.
The original stderr, records and measurements remain intact. Classification
removes only the shared stderr rejection; incorrect output, nonzero exits,
timeouts, cleanup failures and accounting failures still reject the observation.
The shared workload harness retains its strict stderr policy.

```sh
python3 -m unittest discover -s tests -p 'test_vfork_sync.py' -v
```

The default wall limit is ten seconds per target; the production supervisor has
its existing five-second publication allowance. Resource limits and process-tree
cleanup come from the shared runtime workload harness. CI runs two repetitions
of every instrumented case in both runtime profiles.

This fixture demonstrates a runtime dependency directly. The earlier intermittent
Java 17 POSIX_SPAWN timeout had a blocked vfork parent and a child waiting on the
same runtime lock in a separate local observation. Its original CI core and full
wait chain were not captured, so passing this regression alone does not establish
the cause of that historical failure. Java 17 and 21 retain their existing
workload checks and unchanged deadlines.
