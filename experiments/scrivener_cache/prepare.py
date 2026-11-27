"""Build a Scrivener cache study guest from the registered Durante and Debian packages."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from calibrated_limits.prepare import digest
from cpp20_locality.verify import require
from scrivener_cache.bench import TOOL_PATHS
from scrivener_order.prepare import bundle, finish_guest, release_files


def verified_valgrind_files(package, expected_digest):
    require(digest(package) == expected_digest, "different Valgrind package")
    files = {}
    # dpkg-deb -x extracts package data without running maintainer scripts.
    with tempfile.TemporaryDirectory() as directory:
        subprocess.run(["dpkg-deb", "-x", package, directory], check=True)
        for relative in ("usr/bin/valgrind.bin", "usr/libexec/valgrind/cachegrind-amd64-linux",
                         "usr/libexec/valgrind/vgpreload_core-amd64-linux.so"):
            path = Path(directory) / relative
            files["cachegrind/" + path.name] = (path.read_bytes(), path.stat().st_mode & 0o777)
    return files


def prepare(out, current_bundle, valgrind_deb):
    out = out.resolve()
    require(not out.is_relative_to(HERE.parent), "choose a guest directory outside the experiment sources")
    plan = json.loads((HERE / "plan.json").read_bytes())
    files = release_files(current_bundle, plan["bundle"], TOOL_PATHS["current"], include_sdk=True)
    files.update(verified_valgrind_files(valgrind_deb, plan["valgrind_package"]["sha256"]))
    bundle.write_directory(out, files)
    finish_guest(out, plan, HERE.name, TOOL_PATHS)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path)
    parser.add_argument("--current-bundle", required=True, type=Path)
    parser.add_argument("--valgrind-deb", required=True, type=Path)
    args = parser.parse_args()
    prepare(args.out, args.current_bundle, args.valgrind_deb)
