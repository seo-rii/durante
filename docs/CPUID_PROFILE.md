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
