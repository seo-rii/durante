#!/usr/bin/env python3
"""Build the optional QEMU API-4 guest counter; no runtime download or sandbox."""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def build(output, compiler="cc"):
    output = Path(output).absolute()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".durante-qemu-", dir=output.parent) as temp:
        built = Path(temp) / "durante-qemu.so"
        subprocess.run([
            compiler, "-std=c11", "-O2", "-D_FORTIFY_SOURCE=2", "-fstack-protector-strong",
            "-Wall", "-Wextra", "-Werror", "-fPIC", "-fvisibility=hidden", "-shared",
            "-Wl,-z,relro,-z,now,-z,noexecstack", str(ROOT / "qemu/durante.c"),
            "-o", str(built),
        ], check=True)
        built.chmod(0o755)
        os.replace(built, output)
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cc", default="cc")
    args = parser.parse_args()
    print(build(args.output, args.cc))
