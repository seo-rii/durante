# Durante DynamoRIO client

Build `libdurante.so` using the [repository instructions](../README.md).
Launch it with `-outdir ABSOLUTE_PRIVATE_DIRECTORY`, optionally followed by
`-rep_bytes`. Add `-protect_meter` to isolate the counters, code cache and
records from the measured application; this requires the prepared runtime
launched with `-durante_window BASE` (the `durante run --protect-meter` flag
sets both) and reports `meter_protection` in each image's start record. See
`client/meter_protect.h` and `SECURITY.md` for the protected ranges, the
per-store range check and the allow-listed system calls. Add `-disallow_cpu_feature avx` and/or
`-disallow_cpu_feature simd` to stop before selected instructions execute.
Add `-cpuid_profile x86-64-v1` or `x86-64-v2` to report one fixed virtual
processor to application CPUID/XGETBV ([CPUID profiles](../docs/CPUID_PROFILE.md)).
Values are exact lowercase spellings and duplicates are rejected. The earlier
single-file `drmeter -o REPORT` interface is not used.

Each application basic block adds its instruction count to thread-local state.
For ordinary instruction counting, the client can charge every block inline,
including dynamically linked executables, shared libraries and generated code.
This path requires client-owned raw TLS slots and runs with the latency model,
cache probe and count-location profiling disabled. It also supports `-rep_bytes`
and `-live_work_v1`. With `-latency_bound` or its `-memory_operand_bytes`
extension, a callback charges each block, even when the live clock is off.
The inline entry preserves application flags, including values a signal handler
may inspect. Its scratch registers spill to client-owned TLS slots.
A restore-state handler recovers them if the runtime translates a thread inside
the entry. Each memory write records its replay checkpoint as one packed word in
another slot: the low 17 bits of the write's address, its length and its block
suffix, stored by one move of a sign-extended 32-bit immediate with no scratch
register. A block entry stores zero there. Callbacks settle the word into the
checkpoint state before they read or replace it. Each knows the write it
expects (the REP instruction it observes, or the write or continuation the
runtime replays) and takes the full address from it; a word whose low bits do
not match leaves no checkpoint. The low bits identify the write because the word
and the expected write are in the block being executed: every entry clears the
word, and every settling callback names a write of the current block. Traces
are off, and the runtime does not elide jumps or calls for a client, so a block
is about `-max_bb_instrs` (256) contiguous instructions, under 4 KiB. A block whose code still spans 128 KiB
less 16 bytes or more (with `-max_elide_jmp` or `-max_elide_call`) stores no
packed words. A write that does not fit the word (a suffix above 1023
instructions) or such a block takes the callback instead.
`tests/checkpoint_identity.py` runs a build that also stores every write's full
address and aborts if a settled word is kept or dropped differently from the
full address; it covers HotSpot, V8, PyPy, PHP's JIT, `tests/jit_stress.py` and
a fixture that writes its own code. The restore-state handler also returns a
stored entry charge when a relocated
thread will enter the same block again. The layout depends only on the block, so
no translations are stored. A retry's first-block checks move from block entry
to the replay, REP, signal and snapshot callbacks. The entry sequence for each
block size and the write checkpoint are encoded once per process and inserted
as raw copies, with only a write's packed word patched. The
runtime otherwise re-encodes every inserted instruction of each new block, which
dominated block building for interpreters and generated code.

With `-live_work_v1`, the inline entry counts down to the thread's next
publication boundary instead of adding to the instruction count: the countdown
is the only counter it updates. Callbacks add the distance the countdown has
moved to the instruction count before they read it, so they always see the full
count. The entry reads the countdown's sign from its top byte with one MOVZX,
so it still never reads or writes the application's flags. A
negative countdown takes the checked callback, which handles the publication
boundary, replay and additions near overflow and charges the block. All blocks
reach it through one routine, generated once per process. An entry stores its
count and first address in the thread's state and jumps there through a pointer
in that state, with its resume address in RCX. Nothing in the entry depends on
where it is placed, so the whole entry is encoded once per block size and copied
with only the block's address patched; building a block encodes no context
switch. The routine moves to a stack the thread owns, saves the flags and the
general registers a C function may change, clears the flags and calls the
callback. It saves no vector or x87 state: the callback and everything it can
reach are compiled to use the general registers only, and
`tests/test_boundary_callback.py` reads the built client to check that. The
runtime defers asynchronous signals and translation while a thread is in the
routine or the callback, as it does inside a clean call. The prepared runtime
finds the block from the first address the entry stores and unlinks it for a
deferred signal, so the signal is delivered when the thread leaves the block.
Publication therefore
happens at the same block entries as on the callback path. Replay, REP
and new threads reset the countdown so the next entry takes the callback, and the
callback recomputes it. The prefix saves the pre-entry countdown before choosing the
callback or inline charge, and the callback marks an entry it has charged. If
relocation will re-enter an unfinished entry, the restore-state handler takes
that entry's charge back and re-arms a pending REP retry. Work
already published for earlier blocks remains visible.

Without raw TLS, or with the cache probe, the ordinary counter can instead
update that state directly in the original executable text containing a
fixed-address, statically linked program's entry point. It saves and restores
its scratch registers and arithmetic flags even when a signal handler is the
only code that reads them, and other code uses callbacks.

REP MOVS/STOS byte demand is read at the REP instruction after earlier register
updates, with one clean call per REP entry. Conditional REP forms are separately
reported as unsupported byte demand, without inventing a byte charge.

Fork callbacks reset inherited counters. Exec checkpoints preserve work in the
old image and new image records restart counters; failed exec continues its old
cumulative segment. Shared-address-space vfork children use context-local process
state instead of modifying parent globals. The collector selects each completed
segment once and verifies process edges and all terminal records.

Signals remain visible under a basic-block-entry definition; they do not establish
hardware retirement accuracy. Runtime compatibility, process-tree completeness,
counter trust and time-limit semantics are separate questions.

The policy applies at executed instruction boundaries, including generated code,
and is inherited by followed forks and threads. While enabled, application
`execve`/`execveat`, x32 syscalls and unsupported clone forms return `EPERM`;
compatibility syscall instructions stop before execution. Measurement-only
execution retains its existing syscall behavior. A feature violation writes a
`cpu_feature_violation` lifecycle record and exits that process with 126. The
collector returns unavailable work with no partial totals. A normal application
exit 126 does not establish a violation. Policy state and records share the
application's mutable trust domain; this is not tamper-resistant enforcement.
