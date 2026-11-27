"""Build the CPU-pinned guest from the registered Durante and Cachegrind packages."""
import argparse
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE.parent))
from calibrated_limits.prepare import digest
from cpp20_locality.verify import require
from scrivener_cache.bench import TOOL_PATHS
from scrivener_cache.prepare import verified_valgrind_files
from scrivener_order.prepare import bundle, finish_guest, release_files


def prepare(out, current_bundle, valgrind_deb):
    out = out.resolve()
    require(not out.is_relative_to(HERE.parent), "choose a guest directory outside the experiment sources")
    plan = json.loads((HERE / "plan.json").read_bytes())
    files = release_files(current_bundle, plan["bundle"], TOOL_PATHS["current"], include_sdk=True)
    files.update(verified_valgrind_files(valgrind_deb, plan["valgrind_package"]["sha256"]))
    sources = {name: ROOT / relative for name, relative in plan["sources"].items()}
    require(all(path.is_file() and path.resolve().is_relative_to(ROOT) for path in sources.values()),
            "unknown workload source")
    require({name: digest(path) for name, path in sources.items()} == plan["source_sha256"],
            "workload source differs from registration")
    bundle.write_directory(out, files)
    finish_guest(out, plan, HERE.name, TOOL_PATHS, sources=sources)
    # Extract a transferred guest tarball with --no-same-owner as the account that will run the collector.
    for program in sources:
        (out / "binaries" / program).chmod(0o700)


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("out", type=Path)
    cli.add_argument("--current-bundle", required=True, type=Path)
    cli.add_argument("--valgrind-deb", required=True, type=Path)
    args = cli.parse_args()
    prepare(args.out, args.current_bundle, args.valgrind_deb)
