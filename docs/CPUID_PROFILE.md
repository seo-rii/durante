# Fixed CPUID profiles

Instruction counts depend on which implementation a program selects after
reading CPUID. glibc's string functions, libstdc++, OpenSSL, BLAS libraries,
JIT compilers and language runtimes all dispatch on it, so the same program and
input execute different code on different processors. On a fleet whose
processor model is not fixed (for example Cloud Run), this makes the count of
one submission depend on the instance that ran it.

`--cpuid-profile x86-64-v1` or `x86-64-v2` on `durante run` / `isolate`
(client option `-cpuid_profile`) replaces every application CPUID and XGETBV
result with one constant table. The application then observes the same
processor on every host, and its startup dispatch selects the same code.

## Reported processor

| Field | `x86-64-v1` | `x86-64-v2` |
| --- | --- | --- |
| Vendor (leaf 0) | `GenuineIntel` | `GenuineIntel` |
| Signature (leaf 1 EAX) | family 6, model 0, stepping 0 | same |
| Leaf 1 EDX | FPU, TSC, CX8, CMOV, CLFSH, MMX, FXSR, SSE, SSE2 | same |
| Leaf 1 ECX | none | SSE3, SSSE3, CX16, SSE4.1, SSE4.2, POPCNT |
| Extended leaf 1 | SYSCALL, NX, LM | adds LAHF/SAHF |
| Leaves 2-7, 0xB, 0xD, hypervisor range | zero | zero |
| Brand string | `Durante virtual CPU x86-64-v1` | `... x86-64-v2` |
| XGETBV index 0 and 1 | `0x3` (x87, SSE) | same |

The maximum basic leaf is 7 and the maximum extended leaf is `0x80000008`
(48 physical and linear address bits). OSXSAVE, AVX, structured extended
features (BMI, AVX2, ERMS, FSRM, AVX-512, SHA, ...) and cache descriptors are
absent. Every other request returns zeros. Values never depend on the host.

The vendor must be a registered one: glibc does not read leaf 1 for an
unregistered vendor, and its ISA-level check then refuses to load libc
(`CPU ISA level is lower than required`). Family 6 model 0 is not a real model,
so model-specific tuning tables do not match. Cache leaves are empty, so
cache-size heuristics see the same values everywhere; aonohako's C/C++ glibc
tunables still fix copy thresholds explicitly.

Because OSXSAVE is not reported, software does not size XSAVE areas from the
host and uses FXSAVE paths. This is why there is no AVX profile: reporting AVX
requires reporting an XSAVE area size, while the host's XCR0 can enable larger
state components that a hardware XSAVE then writes.

## Host qualification and failure behavior

Before the first application instruction, the client checks that the real
processor provides every reported instruction-set bit (leaf 1 ECX/EDX and
extended leaf 1). A host that lacks one stops the launch with an error; the
profile never reports a feature the host cannot execute.

A program can still execute an instruction the profile does not advertise
(for example AVX2 code compiled with `-mavx2`). The profile does not stop it.
Combine it with `--disallow-cpu-feature avx` or `simd`
([CPU feature restrictions](CPU_FEATURE_POLICY.md)) to stop such execution.

XGETBV with an index other than 0 or 1 returns zeros and makes the
measurement unavailable. On real hardware without OSXSAVE it would fault.

## Accounting

An app2app pass replaces each CPUID/XGETBV with a drmgr emulation region (start
label, clean call, one application NOP with the original translation, end
label). The counter charges the region exactly once, as the original
instruction. Every `start` record carries `cpuid_profile_version: 1` and
`cpuid_profile`; collectors require all images to agree and publish
`cpuid_profile` on complete aggregates. The runner rejects a complete
measurement whose receipt differs from the request. See the
[protocol](PROTOCOL.md).

## Runtime observations

```sh
cmake -S tests/cpuid_profile -B build-cpuid \
  -DDynamoRIO_DIR="$PWD/toolkit/sdk/DynamoRIO-Linux-11.3.0-1/cmake"
cmake --build build-cpuid
python3 tests/cpuid_runtimes.py --sdk toolkit/sdk/DynamoRIO-Linux-11.3.0-1 \
  --client build-cpuid/libcpuid_profile_client.so --output-dir .results/cpuid-runtimes
```

`tests/cpuid_runtimes.py` runs one fixed CPython, Node and Java workload
(sorting, a SHA-256 digest and, for CPython, zlib) natively and under the same
stock-core test client, with ASLR disabled. Every group compares output with a
native run of the same options and reports the exact instruction spread over
repeated fresh processes. The "above-profile mask" removes only features the
profiles never report, through `GLIBC_TUNABLES` and `OPENSSL_ia32cap`, to
simulate a host that lacks them.

Local runs on 2026-11-17 (same host as above, stock release core,
`x86-64-v2`, 3 repetitions per group; Node and Java result SHA-256
`906dae446a429257ec6c0ca67e679c405a205fa0fea1628829036dfc96604156`, CPython
result SHA-256
`5294623f8ac4142d23eaca27262f377fde7b6388b10b328944e2c23adf0f8646`). Every group
exited normally and matched native output. Medians:

| Runtime and options | Host CPUID: mask effect | `x86-64-v2`: mask effect | `x86-64-v2` spread |
| --- | ---: | ---: | ---: |
| CPython 3.13.18 `-s -P`, `PYTHONHASHSEED=0` | 230.40M → 298.83M (+29.7%) | 299,966,809 → 299,977,961 (+0.0037%) | 0.02-0.07 ppm |
| Node 22.23.4 `--predictable` | 224.43M → 258.17M (+15.0%) | 259.28M → 259.31M (+0.013%) | 116-173 ppm |
| OpenJDK 21.0.12 `-Xbatch -XX:+UseSerialGC -XX:ActiveProcessorCount=1` | 1451.1M → 1458.7M (+0.52%) | 1439.6M → 1439.2M (-0.031%) | 300-316 ppm |

With host CPUID visible, the same work changes by up to 30% with features the
host lacks (OpenSSL and glibc dispatch). With the profile, Node's and Java's
changes are within their run-to-run spread. CPython, whose spread is 20
instructions once its seed is pinned, still changes by a constant 11,152
instructions (0.0037%), against 68.4M (+29.7%) with host CPUID; this
observation does not identify the source of that residue. HotSpot reports the virtual processor (`family 6 model 0`,
SSE through SSE4.2, `UseAVX=0`, `UseAES=false`) and V8/OpenSSL select the same
baseline paths.

The profile does not make runtimes repeatable by itself. CPython picks a
random string-hash seed per process: with `-I` (which implies `-E` and so
ignores `PYTHONHASHSEED`) the same workload spread 274 ppm under `x86-64-v2`,
and with the seed pinned through `-s -P` and `PYTHONHASHSEED=0` it spread 20
instructions. An earlier run of this harness passed `-I` together with
`PYTHONHASHSEED=0`, so its CPython seed was not actually pinned. With default
options, Node's counts varied by 20.2% and Java's by 7.7% across identical
runs, from background compilation and GC thread scheduling. The listed options
reduce that to a few hundred ppm; they change the amount of work (Node's
median falls from 389M to 259M instructions), so a time model must be
qualified with the same options. Changing V8 code-generation flags alone added
about 51M instructions under both host CPUID and the profile, which is a
constant cost of a non-default V8 configuration rather than CPU dispatch; the
harness therefore simulates missing host features only through library
dispatch controls.

This is one workload per runtime on one host through the stock-core test
client, not runtime qualification under Durante's lifecycle accounting.

## Not virtualized

- `/proc/cpuinfo`, `AT_HWCAP`/`AT_HWCAP2` and sysfs CPU topology still show the
  host. Software that dispatches on them instead of CPUID is not covered.
- `RDTSC`, `RDTSCP`, `RDRAND`, `RDSEED` and clock reads remain native and
  time-dependent.
- CPUID executed by DynamoRIO or the client itself is not affected.
- The logical processor count (`sched_getaffinity`, `nproc`) comes from the
  kernel. Fix it with the execution owner's CPU set or runtime options.

## Evidence

`tests/cpuid_profile.py` builds a test client that uses the production
emulation header without Durante's runtime patch, so it runs on the stock
SDK core, plus a dynamically linked probe that prints its observed CPUID,
XGETBV, compiler feature detection and then runs glibc string functions.
Host feature differences are simulated with `GLIBC_TUNABLES` masks, which can
only remove features. With ASLR disabled (`setarch -R`), the instructions added
by four more rounds of string work are compared across masks.

Local run on 2026-11-17 (Intel Xeon @ 2.10 GHz with AVX-512, Ubuntu glibc 2.39,
stock 11.3.0-1 debug core):

| Profile | No mask | Mask AVX-512 | Mask AVX-512, AVX2, AVX, ERMS, FSRM |
| --- | ---: | ---: | ---: |
| Host CPUID visible | 819,816 | 885,428 | 2,622,088 |
| `x86-64-v1` | 3,015,288 | 3,015,288 | 3,015,288 |
| `x86-64-v2` | 2,962,860 | 2,962,860 | 2,962,860 |

Without a profile, the same work changes by 3.2x with the host features glibc
can see. With either profile it is identical. Absolute counts with a profile
differ between masks only by the cost of parsing the different
`GLIBC_TUNABLES` strings at startup. With ASLR enabled, repeated identical runs
differed by up to 45 instructions in about 3.3 million (address layout, not
CPUID).

```sh
python3 tests/cpuid_profile.py --sdk toolkit/sdk/DynamoRIO-Linux-11.3.0-1 \
  --output-dir .results/cpuid-profile --engine-debug
python3 tests/cpuid_profile.py --sdk toolkit/sdk/DynamoRIO-Linux-11.3.0-1 \
  --client build/libdurante.so --runner-bin target/release/durante \
  --output-dir .results/cpuid-profile-production
```

The second form also runs the production client through `durante run` and
checks receipts and complete counts; it needs the prepared runtime. Those
production-client results were not collected in the local run above, because
the patched runtime could not be built there; CI runs both forms. The
simulation does not replace a campaign on hosts with different processor
vendors and generations, which remains required before relying on a profile
for a fleet.
