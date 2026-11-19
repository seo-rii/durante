"""Run the assembly control that observes live publication at basic-block boundaries."""
import argparse
from pathlib import Path
import subprocess
import tempfile

from compare import clocks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("drrun", "client", "program"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as temporary:
        output = Path(temporary).resolve()
        live = output / "live"
        live.write_bytes(bytes(48))
        subprocess.run([
            args.drrun.resolve(strict=True), "-quiet", "-no_cleancall_ignore_eflags", "-disable_traces",
            "-vm_size", "32M", "-vmheap_size", "32M",
            "-c", args.client.resolve(strict=True), "-outdir", output, "-rep_bytes",
            "-probe_l1d", "-live_work_v1", live, "--", args.program.resolve(strict=True),
        ], stdin=subprocess.DEVNULL, cwd=output, check=True, timeout=20)
        if clocks(output)["rep_stos_bytes"] != 4113:
            raise ValueError("unexpected final STOS byte count")


if __name__ == "__main__":
    main()
