"""Materialize the frozen guest payload from the release installation and archived ELFs."""
import argparse
import json
from pathlib import Path
import shutil
import tarfile
import tempfile
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from cpp20_locality.fixtures import manifest
from cpp20_locality.verify import REPO, require, sha, verify_elfs


def prepare(out, release, near, span, elf_archive, reader):
    sources = {"v11.so": release / "lib/libdurante.so", "near.so": near, "span.so": span,
               "production_reader.py": reader}
    materialize(out, release, elf_archive, HERE, sources)


def materialize(out, release, elf_archive, study, sources):
    """Build a new payload only when all inputs match the published protocol."""
    require(not out.exists(), f"output already exists: {out}")
    protocol = (study / "protocol.json").read_bytes()
    digests = json.loads(protocol)["tools_sha256"]
    verify_elfs(elf_archive, manifest())
    with tempfile.TemporaryDirectory(dir=out.parent) as temp, tarfile.open(elf_archive, "r:xz") as elfs:
        staging = Path(temp) / "payload"
        staging.mkdir()
        for name, expected in digests.items():
            target = staging / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if name.startswith("bin/"):
                target.write_bytes(elfs.extractfile(name).read())
                target.chmod(0o755)
            else:
                origin = release / name if name.startswith("dynamorio/") else sources.get(name, study / name)
                shutil.copy2(origin, target)
            require(sha(target.read_bytes()) == expected, "payload differs from preregistration: " + name)
        (staging / "protocol.json").write_bytes(protocol)
        checks = {**digests, "protocol.json": sha(protocol)}
        (staging / "checksums.sha256").write_text("".join(f"{checks[n]}  {n}\n" for n in sorted(checks)))
        staging.rename(out)


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("out", type=Path)
    cli.add_argument("--release", required=True, type=Path, help="v11 installation with lib/ and dynamorio/")
    cli.add_argument("--near", required=True, type=Path, help="original libaccess_sample.so")
    cli.add_argument("--span", required=True, type=Path, help="new libaccess_span.so")
    cli.add_argument("--elf-archive", required=True, type=Path, help="Aonohako #154 elfs.tar.xz")
    cli.add_argument("--reader", type=Path, default=REPO / "collect.py", help="release collect.py")
    args = cli.parse_args()
    prepare(args.out, args.release, args.near, args.span, args.elf_archive, args.reader)
