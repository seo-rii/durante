#!/usr/bin/env python3
"""Pack trusted builds, relocate them, and execute one bounded native map probe."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from concurrent_spawn import cleanup_orphans, own_subreaper

ROOT = Path(__file__).resolve().parents[1]


def run(command, timeout=20):
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env={"PATH": os.defpath, "LANG": "C", "LC_ALL": "C"}, timeout=timeout)
    if result.returncode or result.stderr or len(result.stdout) > 65536:
        raise ValueError(f"deployment smoke command failed: {Path(command[0]).name}: {result.stderr[:1000]!r}")
    return result.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("sdk", "client", "cli", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--launcher", type=Path)
    parser.add_argument("--hide-sdk", action="store_true", help="CI-owned SDK only: temporarily rename and restore it")
    args = parser.parse_args()
    work = args.output_dir.absolute()
    work.mkdir(mode=0o700)
    sdk = args.sdk.resolve(strict=True)
    libc, previous = own_subreaper()
    hidden = None
    result = {"passed": False, "authority": "diagnostic_only", "integrity_verified": False,
              "original_sdk_hidden": args.hide_sdk}
    try:
        archive = work / "bundle.tar.gz"
        package = [sys.executable, str(ROOT / "toolkit/bundle.py"), "pack", "--sdk", str(sdk),
                   "--client", str(args.client.resolve()), "--cli", str(args.cli.resolve()),
                   "--output", str(work / "packed"), "--archive", str(archive)]
        if args.launcher:
            package += ["--launcher", str(args.launcher.resolve())]
        packed = json.loads(run(package))
        result["package"] = packed
        result["verification"] = json.loads(run([sys.executable, str(ROOT / "toolkit/bundle.py"), "unpack",
            "--archive", str(archive), "--sha256", packed["archive_sha256"],
            "--source-revision", packed["source_revision"], "--output", str(work / "unpacked")]))
        relocated = work / "relocated version"
        (work / "unpacked").rename(relocated)
        if args.hide_sdk:
            hidden = Path(tempfile.mkdtemp(prefix="durante-hidden-build-sdk-", dir=sdk.parent)) / "sdk"
            sdk.rename(hidden)
        entrypoint = str(relocated / "bin/durante-bundle")
        result["doctor"] = json.loads(run([entrypoint, "doctor"]))
        if result["doctor"].get("checks_passed") is not True or result["doctor"].get("execution_tested") is not False:
            raise ValueError("relocated doctor result is invalid")
        probe = work / "probe"
        run(["gcc", "-D_GNU_SOURCE", "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror",
             str(ROOT / "tests/bundle_probe.c"), "-o", str(probe)])
        report = work / "run.json"
        maps = run([entrypoint, "run", "--report", str(report), "--timeout-ms", "5000", "--", str(probe)])
        (work / "maps.txt").write_bytes(maps)
        execution = json.loads(report.read_bytes())
        measurement = execution["measurement"]
        if (execution["execution"]["state"] != "complete" or execution["execution"]["cleanup_complete"] is not True
                or execution["execution"]["exit_code"] != 0 or measurement.get("valid") is not True
                or measurement.get("complete") is not True or type(measurement.get("instructions")) is not int
                or measurement["instructions"] <= 0 or measurement.get("authority") != "diagnostic_only"
                or measurement.get("integrity_verified") is not False):
            raise ValueError("relocated execution did not produce complete diagnostic work")
        loaded = {}
        for library in ("libdurante.so", "libdrmgr.so", "libdynamorio.so"):
            paths = {line.split(maxsplit=5)[5].strip() for line in maps.decode().splitlines()
                     if len(line.split(maxsplit=5)) == 6 and line.endswith("/" + library)}
            if not paths or any(not Path(path).is_relative_to(relocated) for path in paths):
                raise ValueError("runtime library resolved outside relocated bundle: " + library)
            loaded[library] = sorted(paths)
        result.update(passed=True, loaded_libraries=loaded, measurement=measurement)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        result["error"] = str(error)[:2000]
    finally:
        if hidden is not None and hidden.exists():
            hidden.rename(sdk)
            hidden.parent.rmdir()
        result["cleanup_complete"] = cleanup_orphans()
        libc.prctl(36, previous, 0, 0, 0)
        result["passed"] = result["passed"] and result["cleanup_complete"]
        (work / "results.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"passed": result["passed"], "error": result.get("error"), "cleanup_complete": result["cleanup_complete"]}))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
