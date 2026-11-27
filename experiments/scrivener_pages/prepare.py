"""Assemble the registered Scrivener workloads, v12 client and branch profiler."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from calibrated_limits.prepare import digest
from cpp20_locality.verify import require
from scrivener_cache.bench import TOOL_PATHS
from scrivener_order.prepare import bundle, finish_guest


def file_entry(path):
    return path.read_bytes(), path.stat().st_mode & 0o777


def prepare(output, runtime, cachegrind, *, study=HERE):
    output = output.resolve()
    require(not output.is_relative_to(HERE.parent) and not output.exists(),
            "choose a fresh output directory outside the experiment sources")
    plan = json.loads((study / "plan.json").read_bytes())
    tools = dict(plan["tools"])
    scan_sha256 = tools.pop("scan")
    files = {TOOL_PATHS["current"]: file_entry(runtime / "lib/libdurante.so")}
    for relative, expected in plan["runtime_sha256"].items():
        source = runtime / "dynamorio" / bundle.safe_name(relative)
        require(digest(source) == expected, "different runtime file: " + relative)
        files["dynamorio/" + relative] = file_entry(source)
    for key in ("valgrind", "cachegrind", "preload"):
        relative = TOOL_PATHS[key]
        files[relative] = file_entry(cachegrind / Path(relative).name)

    with tempfile.TemporaryDirectory(dir=output.parent) as temporary:
        staging = Path(temporary)
        scanner = staging / "scan"
        subprocess.run(["g++", "-std=c++20", "-O2", "-static", "-s", "-march=x86-64-v2",
                        "-Wl,--build-id=none", study / "scan.cpp", "-o", scanner], check=True)
        require(digest(scanner) == scan_sha256, "different static scan ELF")
        payload = staging / "payload"
        bundle.write_directory(payload, files)
        finish_guest(payload, dict(plan, tools=tools), study.name, TOOL_PATHS)
        shutil.move(scanner, payload / "binaries/scan")
        with (payload / "checksums.sha256").open("a") as manifest:
            manifest.write(f"{scan_sha256}  binaries/scan\n")
        payload.rename(output)


def main(study=HERE):
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--runtime", type=Path, required=True, help="worker install containing dynamorio/ and lib/")
    cli.add_argument("--cachegrind", type=Path, required=True, help="directory with the three registered Debian ELFs")
    cli.add_argument("--output", type=Path, required=True)
    args = cli.parse_args()
    prepare(args.output, args.runtime, args.cachegrind, study=study)


if __name__ == "__main__":
    main()
