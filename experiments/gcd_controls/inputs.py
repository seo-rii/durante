#!/usr/bin/env python3
"""Reconstruct the five registered stdin files without collecting a timing."""
import argparse
from pathlib import Path
import runpy

from study import HERE, input_manifest, plan, read_inputs, require


def materialize(directory):
    value = plan()
    directory.mkdir(parents=True)
    stream = runpy.run_path(str(HERE/"stream/fixtures.py"))
    with (directory/"stream.bin").open("xb") as output:
        stream["input_oracle"](output)
    loops = runpy.run_path(str(HERE/"loops/fixtures.py"))
    tables, _, _, _ = loops["inputs"]()
    for order, raw in tables.items():
        (directory/f"loop_{order}.bin").write_bytes(raw)
    for case in value["cases"]:
        if "stdin_hex" in case:
            path = directory/case["input_file"]
            raw = bytes.fromhex(case["stdin_hex"])
            if path.exists():
                require(path.read_bytes() == raw, "conflicting literal input")
            else:
                path.write_bytes(raw)
    manifest = input_manifest(value)
    require({p.name for p in directory.iterdir()} == set(manifest), "unexpected generated files")
    read_inputs(directory, manifest)
    for path in directory.iterdir():
        path.chmod(0o444)
    print(f"Verified {len(manifest)} distinct input files ({sum(v['length'] for v in manifest.values())} bytes).")


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("directory", type=Path, help="fresh directory in which to create the registered inputs")
    materialize(cli.parse_args().directory)
