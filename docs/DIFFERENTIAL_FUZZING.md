# Differential fuzzing and exact replay

The differential harness runs the production Rust CLI and Python reference on the
same generated record bytes. It checks matching validity, exact accepted results,
consistent exit status and absence of partial totals on rejection. Rejection
messages may differ. These are diagnostic checks; agreement does not establish
measurement integrity or qualify an untrusted workload for billing.

Run a bounded campaign with a built native collector:

```sh
python3 tests/differential_collect.py --collector-bin target/release/durante \
  --cases 512 --seed 20261111 --failure-dir .results/differential-failure
```

The first disagreement, malformed result, nonempty stderr, launch error, crash,
timeout or output overflow ends the campaign with exit status 1 and saves a bundle.
Both collectors are attempted, so a native crash still retains the reference
result. A successful campaign exits 0. Setup errors, an invalid replay bundle or
failure to save a bundle exit 2. An unsuccessful run never becomes a passing result
just because its failure artifact could not be saved.

The failure directory must not already exist, even when a run would pass. Choose
a fresh name for each run; the harness never silently overwrites or removes prior
evidence. It creates that directory only when a comparison fails. CI uploads it
as the `differential-failure` artifact when the differential step fails, before any
SDK build. Setup failures before a case is executed may have no bundle; their
nonzero step status and console diagnostic remain available.

## Replay without the original mutator or executable

Extract a CI artifact into its own directory, or use a local failure directory:

```sh
python3 tests/differential_collect.py --collector-bin target/release/durante \
  --replay .results/differential-failure \
  --failure-dir .results/differential-replay-failure
```

Replay uses the exact saved bytes and root PID, without regenerating a case from
the seed. It runs the native collector supplied by the caller and this checkout's
`collect.py` under the current Python interpreter. Saved executable paths are
metadata and are never executed. No bundle content is interpreted as a command.

Exit 0 means the current collectors agree on the preserved case. It does not
invalidate the earlier failure: compare the reported original/current identities
and the original output files. Exit 1 means a current failure was saved into the
new failure directory, with the original seed/index context retained. Exit 2 means
the replay could not be performed safely. Replay never modifies the original
bundle. A saved failure can itself be replayed, subject to the same metadata bound.

## Bundle layout and bounds

`manifest.json` uses schema 1 and records the root PID, seed, zero-based case
index, baseline vector, mutation, comparison failure, subprocess exit/capture
status and tool identities before/after execution. Replay context retains the
original context. `records/` contains the exact input files; `native.stdout`,
`native.stderr`, `reference.stdout` and `reference.stderr` retain raw output bytes,
including invalid UTF-8 or incomplete JSON. The manifest includes sizes and
SHA-256 digests for every record and output file.

| Item | Bound |
| --- | --- |
| Record files | 1–257; only `process-` + 1–10 decimal digits + `.jsonl` |
| Each record | 1 MiB + 1 byte, allowing an over-limit regression input |
| All records | 16 MiB |
| Each of the four output streams | 1 MiB |
| Manifest | 256 KiB; integer JSON numbers and unique keys |
| Entire file payload | At most 20.25 MiB |

Inputs are never truncated. Output exceeding its limit is saved as a prefix and
marked `output_limit`; all captured streams may be incomplete after termination.
Collectors have a three-second deadline and a one-second termination wait. The
campaign has a 120-second deadline checked between cases. Timeout and overflow
stop the collector's original process group and record whether its direct child
was reaped. The executable and Python collector must be trusted: this harness is
not a sandbox for arbitrary programs or escaping descendants.

New bundle directories use mode `0700` and files `0600`, subject to the caller's
umask. Keep their parent directory under operator control. The manifest is written
last; an interrupted or partial write is rejected on replay. Artifact download
tools may change permission bits; reapply private permissions before sharing a
machine with other users if the inputs contain sensitive data.

Replay rejects missing/extra entries, duplicate names or JSON keys, unsupported
schemas, path traversal, symlinked bundle/record directories, and symlinked,
hardlinked, non-regular or oversized files. It pins directory descriptors, bounds
reads, checks file stability and verifies digests, then copies validated bytes to
a private temporary directory before invoking collectors. It does not provide a
race-proof filesystem sandbox against another process acting as the same user.

## What identities prove

The manifest hashes the supplied native executable, `collect.py`, current Python
executable, harness and protocol vectors, and records Python's version and the
checkout's Git HEAD when available. Changed identities fail a campaign comparison.
The output remains diagnostic: hashes are not signatures, a saved manifest is not
authenticated, a Git revision may have local changes, and a native binary hash
does not prove which source or dependencies built it. Shared libraries and the
host environment are not attested. Preserve build/CI provenance separately when
investigating an environment-dependent disagreement.

The unit suite injects disagreements, partial totals, malformed output, crashes,
launch errors, timeouts and output floods. It also exercises unsafe bundle entries,
digest/size failures and replay using a replacement collector after deleting the
original executable. Run it without an SDK build:

```sh
python3 -m unittest discover -s tests -p 'test_differential_collect.py' -v
```

The independent Rust libFuzzer targets and their crash corpus are described in
[SECURITY.md](../SECURITY.md); their input formats differ from these bundles.
