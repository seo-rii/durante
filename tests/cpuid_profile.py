#!/usr/bin/env python3
"""Check the fixed CPUID profile under a real DynamoRIO core.

The test client in tests/cpuid_profile uses the production emulation header
without Durante's runtime patch, so it also runs on the stock SDK core. It
checks three things:

1. The probe observes exactly the fixed table (CPUID, XGETBV, compiler CPU
   feature detection) and still produces the native output.
2. glibc's startup dispatch no longer follows host features. Host feature
   differences are simulated with GLIBC_TUNABLES masks, which can only remove
   features. Work per extra round of string operations must be identical for
   every mask with a profile, while the host-visible control must change.
3. ASLR is disabled with setarch -R for (2), because address layout causes a
   separate, small, CPU-independent variation.

With --client and --runner-bin, the same checks also run through the
production client and `durante run --cpuid-profile`, which require the
prepared (patched) runtime. That path additionally checks the lifecycle
receipt and that complete counts are published only with the requested
profile.

Exit 0 means all checks passed. A host without AVX2 cannot demonstrate the
control and is reported as such rather than as a pass of that control.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
VENDOR = [0x756E6547, 0x49656E69, 0x6C65746E]  # EBX, EDX, ECX: "GenuineIntel"
EDX1 = 0x07888111
ECX1 = {"x86-64-v1": 0, "x86-64-v2": 0x00982201}
EXT1 = {"x86-64-v1": [0, 0, 0, 0x20100800], "x86-64-v2": [0, 0, 1, 0x20100800]}
MASKS = ("", "glibc.cpu.hwcaps=-AVX512F,-AVX512VL,-AVX512BW",
         "glibc.cpu.hwcaps=-AVX512F,-AVX512VL,-AVX512BW,-AVX2,-AVX,-ERMS,-FSRM")


def expected_leaves(profile):
    brand = f"Durante virtual CPU {profile}".encode().ljust(48, b"\0")
    words = [int.from_bytes(brand[i:i + 4], "little") for i in range(0, 48, 4)]
    zero = [0, 0, 0, 0]
    return {
        "0.0": [7, VENDOR[0], VENDOR[2], VENDOR[1]],
        "0x1.0": [0x600, 0x00010800, ECX1[profile], EDX1],
        "0x2.0": zero, "0x4.0": zero, "0x7.0": zero, "0x7.1": zero, "0xb.0": zero,
        "0xd.0": zero, "0xd.1": zero, "0x40000000.0": zero,
        "0x80000000.0": [0x80000008, 0, 0, 0], "0x80000001.0": EXT1[profile],
        "0x80000002.0": words[0:4], "0x80000003.0": words[4:8],
        "0x80000004.0": words[8:12], "0x80000006.0": zero,
        "0x80000008.0": [0x3030, 0, 0, 0],
    }


def run(command, env=None, timeout=300):
    merged = {k: v for k, v in os.environ.items() if k != "GLIBC_TUNABLES"}
    merged.update(env or {})
    return subprocess.run(command, env=merged, capture_output=True, text=True,
                          timeout=timeout, check=False)


class Harness:
    def __init__(self, sdk, output, debug):
        self.sdk = sdk
        self.output = output
        self.debug = debug
        self.build = output / "build"
        self.sequence = 0

    def compile(self):
        configure = run(["cmake", "-S", str(ROOT / "tests/cpuid_profile"), "-B", str(self.build),
                         "-DCMAKE_BUILD_TYPE=Release", f"-DDynamoRIO_DIR={self.sdk / 'cmake'}"])
        if configure.returncode:
            raise SystemExit(configure.stdout + configure.stderr)
        build = run(["cmake", "--build", str(self.build), "-j2"])
        if build.returncode:
            raise SystemExit(build.stdout + build.stderr)
        self.client = self.build / "libcpuid_profile_client.so"
        self.probe = self.build / "cpuid_profile_probe"

    def instrumented(self, profile, rounds, mask="", no_aslr=False):
        self.sequence += 1
        result = self.output / f"count-{self.sequence}.json"
        command = [str(self.sdk / "bin64/drrun")]
        if self.debug:
            command.append("-debug")
        command += ["-quiet", "-disable_traces", "-c", str(self.client), "-profile", profile,
                    "-out", str(result), "--", str(self.probe), str(rounds)]
        if no_aslr:
            command = ["setarch", "-R", *command]
        process = run(command, {"GLIBC_TUNABLES": mask} if mask else None)
        if process.returncode != 0 or not result.exists():
            raise AssertionError(f"instrumented run failed: {command}\n{process.stderr[-4000:]}")
        count = json.loads(result.read_text())
        return json.loads(process.stdout), count

    def production(self, runner, client, profile, rounds, mask="", no_aslr=False):
        """Run through `durante run`; returns probe output and the measurement."""
        self.sequence += 1
        report = self.output / f"report-{self.sequence}.json"
        command = [str(runner), "run", "--drrun", str(self.sdk / "bin64/drrun"),
                   "--client", str(client), "--report", str(report), "--timeout-ms", "120000"]
        if profile != "none":
            command += ["--cpuid-profile", profile]
        command += ["--", str(self.probe), str(rounds)]
        if no_aslr:
            command = ["setarch", "-R", *command]
        process = run(command, {"GLIBC_TUNABLES": mask} if mask else None)
        if process.returncode != 0 or not report.exists():
            raise AssertionError(f"durante run failed: {command}\n{process.stderr[-4000:]}")
        result = json.loads(report.read_text())
        return json.loads(process.stdout), result


def production_checks(harness, runner, client, native, failures):
    rows = {}
    for profile in ("none", "x86-64-v1", "x86-64-v2"):
        rows[profile] = []
        for mask in MASKS:
            deltas = []
            for rounds in (4, 8):
                observed, result = harness.production(runner, client, profile, rounds, mask,
                                                      no_aslr=True)
                measurement = result["measurement"]
                expected_receipt = None if profile == "none" else profile
                checks = {
                    "complete": measurement.get("valid") is True and
                                measurement.get("complete") is True,
                    "request_receipt": result.get("cpuid_profile") == expected_receipt,
                    "measurement_receipt": measurement.get("cpuid_profile") == expected_receipt,
                    "output_preserved": observed["checksum"] == native[rounds]["checksum"],
                    "exit_zero": result["execution"].get("exit_code") == 0,
                }
                if profile != "none":
                    checks["cpuid_table"] = observed["cpuid"] == expected_leaves(profile)
                    checks["xcr0"] = observed["xcr0"] == 3
                failures += [f"production {profile} mask={mask!r} rounds={rounds}: {name}"
                             for name, ok in checks.items() if not ok]
                deltas.append(measurement.get("instructions"))
            rows[profile].append({"mask": mask, "rounds_4": deltas[0], "rounds_8": deltas[1],
                                  "per_4_rounds": None if None in deltas else deltas[1] - deltas[0]})
        if profile != "none":
            values = {row["per_4_rounds"] for row in rows[profile]}
            if len(values) != 1 or None in values:
                failures.append(f"production {profile}: dispatch work changed {sorted(map(str, values))}")
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--engine-debug", action="store_true",
                        help="use the debug core (required when no release core is installed)")
    parser.add_argument("--client", type=Path, help="production libdurante.so")
    parser.add_argument("--runner-bin", type=Path, help="durante CLI")
    args = parser.parse_args()
    if (args.client is None) != (args.runner_bin is None):
        raise SystemExit("--client and --runner-bin must be supplied together")
    if not shutil.which("setarch"):
        raise SystemExit("setarch (util-linux) is required")
    args.output_dir.mkdir(mode=0o700)
    harness = Harness(args.sdk.resolve(), args.output_dir.resolve(), args.engine_debug)
    harness.compile()
    report = {"engine_core": "debug" if args.engine_debug else "release", "profiles": {},
              "dispatch": {}, "passed": False}
    native = run([str(harness.probe), "4"])
    if native.returncode:
        raise SystemExit(native.stderr)
    native = json.loads(native.stdout)
    report["native"] = native
    failures = []
    for profile in ("x86-64-v1", "x86-64-v2"):
        observed, count = harness.instrumented(profile, 4)
        checks = {
            "cpuid_table": observed["cpuid"] == expected_leaves(profile),
            "xcr0": observed["xcr0"] == 3,
            "no_avx": not any(observed["supports"][k] for k in ("avx", "avx2", "avx512f")),
            "sse2": observed["supports"]["sse2"] == 1,
            "sse4_2_matches_level": observed["supports"]["sse4.2"] == (profile == "x86-64-v2"),
            "vendor": observed["is_intel"] == 1 and observed["is_amd"] == 0,
            "output_preserved": observed["checksum"] == native["checksum"],
            "no_invalid_requests": count["invalid_requests"] == 0,
            "receipt": count["profile"] == profile,
        }
        report["profiles"][profile] = {"observed": observed, "count": count, "checks": checks}
        failures += [f"{profile}: {name}" for name, ok in checks.items() if not ok]
    for profile in ("none", "x86-64-v1", "x86-64-v2"):
        rows = []
        for mask in MASKS:
            _, short = harness.instrumented(profile, 4, mask, no_aslr=True)
            _, long = harness.instrumented(profile, 8, mask, no_aslr=True)
            rows.append({"mask": mask, "rounds_4": short["instructions"],
                         "rounds_8": long["instructions"],
                         "per_4_rounds": long["instructions"] - short["instructions"]})
        report["dispatch"][profile] = rows
        deltas = {row["per_4_rounds"] for row in rows}
        if profile != "none" and len(deltas) != 1:
            failures.append(f"{profile}: dispatch work changed with host feature mask {sorted(deltas)}")
    if args.client:
        natives = {4: native}
        longer = run([str(harness.probe), "8"])
        if longer.returncode:
            raise SystemExit(longer.stderr)
        natives[8] = json.loads(longer.stdout)
        report["production"] = production_checks(harness, args.runner_bin.resolve(),
                                                  args.client.resolve(), natives, failures)
    host_has_avx2 = native["supports"]["avx2"] == 1
    control_changed = len({row["per_4_rounds"] for row in report["dispatch"]["none"]}) > 1
    report["control"] = ("demonstrated" if control_changed else
                         "not_demonstrated_without_avx2" if not host_has_avx2 else "failed")
    if report["control"] == "failed":
        failures.append("host-visible control did not change with feature masks")
    report["failures"] = failures
    report["passed"] = not failures
    (args.output_dir / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"passed": report["passed"], "control": report["control"],
                      "failures": failures,
                      "per_4_rounds": {p: [r["per_4_rounds"] for r in rows]
                                       for p, rows in report["dispatch"].items()}}, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
