"""Capture the memory access profiles of a trusted deterministic single-process static x86-64 executable."""
import argparse
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from cpp20_locality.verify import require
from first_run_sampler.profile import elf_digest
from guarded_native import anchor
from guarded_sampler import run as guarded
from shape_calibration import combined, temporal


def capture(payload, probe, elf, stdin, out):
    binary = anchor.static_elf(elf)
    expected_probe = json.loads((HERE / "rules.json").read_bytes())["probe_sha256"]
    if probe is not None:
        require(elf_digest(probe) == expected_probe, "different temporal probe")
    out.mkdir(mode=0o700, parents=False)
    # A fresh cache requests the complete #82 sample, including sites absent on earlier inputs.
    full = guarded.run(payload, elf, stdin, out / "profile-cache", out / "full")
    row, = full["launches"]
    require(row["mode"] == "full" and full["elf_sha256"] == binary and row["exit_code"] == 0,
            "invalid spatial capture")
    extra = None
    if probe is not None:
        extra = temporal.capture(payload, probe, elf, out / "full/stdin", out / "temporal")
        require(extra["elf_sha256"] == binary and extra["stdin_sha256"] == full["stdin_sha256"]
                and extra["stdout_sha256"] == row["stdout_sha256"] and extra["probe_sha256"] == expected_probe,
                "temporal and spatial capture identities differ")
    return row, extra


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    for name in ("payload", "elf", "stdin", "out"):
        cli.add_argument("--" + name, required=True, type=Path)
    cli.add_argument("--probe", type=Path, help="registered temporal sampler; omit for spatial profiles only")
    cli.add_argument("--separate", action="store_true", help="run each sampler in its own process for comparison")
    args = cli.parse_args()
    capture_run = combined.capture if args.probe and not args.separate else capture
    first, extra = capture_run(args.payload.resolve(), args.probe.resolve() if args.probe else None,
                           args.elf.resolve(), args.stdin.resolve(), args.out.resolve())
    print(json.dumps({"spatial_pid": first["launcher_pid"], "temporal_pid": extra["pid"] if extra else None}))
