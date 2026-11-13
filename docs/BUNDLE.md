# Versioned runtime bundles

The bundle tool packages existing, operator-trusted Linux x86-64 builds. It does
not download artifacts, publish releases, install a service, create cgroups, or
change host policy. Build the SDK, client and CLI together using the repository
README. A bundle is a consistency-checked deployment unit, not a signed build
attestation or a trustworthy instruction meter.

```sh
python3 toolkit/bundle.py pack \
  --sdk toolkit/sdk/DynamoRIO-Linux-11.3.0-1 \
  --client build/libdurante.so --cli target/release/durante \
  --output /operator-owned/staging/durante-version \
  --archive /operator-owned/staging/durante-version.tar.gz
```

Both output paths must be new, with existing operator-owned parent directories.
The tool never overwrites them; failed packaging may retain a partial fresh
directory/archive for inspection. Add `--launcher /trusted/bwrap` to include its
matching adjacent `.durante-isolation.json` and license. The optional launcher
must come from `toolkit/build_isolation.py`; packaging checks it with the Rust
CLI's existing validator, independently of whether this machine permits isolation.

Packaging invokes `doctor` from the explicitly trusted CLI and inspects the
client's ELF dependencies with `readelf`. It rejects unprepared/mismatched SDKs,
changed source inputs, altered cores, missing strong private-ABI imports,
unsupported client dependencies and embedded RPATH/RUNPATH. The SDK inventory includes both patched runtime profiles and their
symbols, frontend compatibility files, drmgr, preload libraries and upstream
license notices. Build headers, static libraries, unrelated extensions and tools
are omitted; this runtime subset is not an SDK for compiling another client.

`durante-bundle.json` records the repository version, source commit/tree, bundle
recipe, reference component build commands, private ABI version and each file's
SHA-256, length and mode. The SDK and optional launcher manifests retain their
exact source/patch/recipe provenance. The reference build commands and source
revision describe the builder's selected checkout; hashes alone cannot prove a
prebuilt binary was compiled from that source. Use trusted build infrastructure
and protect the complete version directory from modification while it is in use.
Packaging/runtime sources and included documentation must match committed files;
their hashes are also recorded and checked against the release verifier checkout.

## Static verification and relocation

Transport the reported archive SHA-256 and full source revision through a trusted
channel, separately from untrusted artifact storage. Use a verifier checkout for
that release: it requires the reviewed runtime source, both patches, build recipe
and bundle version. The verification commands below never execute bundled files.

```sh
python3 toolkit/bundle.py verify --archive durante-version.tar.gz \
  --sha256 "$EXPECTED_ARCHIVE_SHA256" --source-revision "$EXPECTED_SOURCE_REVISION"
python3 toolkit/bundle.py unpack --archive durante-version.tar.gz \
  --sha256 "$EXPECTED_ARCHIVE_SHA256" --source-revision "$EXPECTED_SOURCE_REVISION" \
  --output /operator-owned/versions/durante-version
python3 toolkit/bundle.py verify-directory \
  --directory /operator-owned/versions/durante-version \
  --source-revision "$EXPECTED_SOURCE_REVISION"
```

Archives contain regular files only. Duplicate/absolute/traversing paths, links,
devices, unexpected/missing files, incorrect modes and changed hashes are rejected
before extraction. Limits are 128 entries, directory depth eight, 64 MiB per file and 256 MiB compressed,
expanded and payload bytes. Extraction writes only to a newly created private
directory. `verify-directory` detects inconsistent installed files; it does not
authenticate a manifest that a malicious installer also rewrote. Filesystem
ancestors must remain operator-controlled throughout verification and execution.

The shell entrypoint resolves its physical version directory once and supplies
the matching CLI, client, SDK and optional launcher paths:

```sh
/operator-owned/versions/durante-version/bin/durante-bundle doctor
/operator-owned/versions/durante-version/bin/durante-bundle run \
  --report /protected/new-report.json --timeout-ms 5000 -- /trusted/program
/operator-owned/versions/durante-version/bin/durante-bundle doctor-isolate
```

`isolate` additionally requires the same external execution owner and bounded
cgroup environment described in [isolation prerequisites](ISOLATION.md).
The included [execution-owner guide](EXECUTION_OWNER.md) describes the owner's
responsibilities, tested recovery scenarios and remaining qualification limits.
The bundle does not supply a guardian, aggregate lifetime/disk/I/O enforcement
or controller-death recovery.
The raw CLI remains at `bin/durante`; `collect`, `--version` and `--help` are also
available through the wrapper. Python is needed for packaging/verification only,
not for launching an installed bundle. Host glibc/system libraries, kernel and
runtime compatibility remain prerequisites; this is not a self-contained OS image.

`doctor` is a prerequisite check, not an execution qualification. The dedicated
bundle CI executes a small native fixture after relocation, checks complete
diagnostic counts and confirms from the running target's `/proc/self/maps` that
Durante, drmgr and DynamoRIO are loaded from the relocated version. It also hides
its own original SDK for that check. This establishes the fixed deployment smoke
case, not support for every application or an escape-free sandbox.

## Upgrade and rollback

1. Keep the current version intact. Build and unpack the candidate into a separate
   new version directory, then verify it against the expected revision and digest.
2. Run `doctor` and bounded execution acceptance tests in the actual service image,
   with its normal external owner, resource limits and representative workloads.
3. Point newly admitted jobs at the candidate's full version path. Keep each job
   on the version it selected; do not replace individual binaries or SDK files.
4. For rollback, direct new jobs to the previous complete version directory. Drain
   jobs using the rejected version before an operator removes its files.

An operator-managed pointer can be switched atomically by the deployment system,
but this tool does not update it or delete older versions. Store result/report
files outside version directories and retain bounded failure evidence according
to [operator recovery](OPERATIONS.md). Successful verification, smoke tests and rollback all
retain `authority: diagnostic_only` and `integrity_verified: false`.
