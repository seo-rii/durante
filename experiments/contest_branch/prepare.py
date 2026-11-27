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


def prepare(out, current_bundle, valgrind_deb, *, study=HERE):
    out = out.resolve()
    require(not out.is_relative_to(HERE.parent), "choose a guest directory outside the experiment sources")
    plan = json.loads((study / "plan.json").read_bytes())
    files = release_files(current_bundle, plan["bundle"], TOOL_PATHS["current"], include_sdk=True)
    files.update(verified_valgrind_files(valgrind_deb, plan["valgrind_package"]["sha256"]))
    sources = {name: ROOT / relative for name, relative in plan["sources"].items()}
    require(all(path.is_file() and path.resolve().is_relative_to(ROOT) for path in sources.values()),
            "unknown workload source")
    require({name: digest(path) for name, path in sources.items()} == plan["source_sha256"],
            "workload source differs from registration")
    expected_elfs = dict(plan["binaries"])
    if "probe" in plan:
        probe = plan["probe"]
        name = Path(probe["path"]).name
        source = ROOT / probe["source"]
        require(probe["path"] == f"binaries/{name}" and name not in sources
                and source.resolve().is_relative_to(ROOT) and digest(source) == probe["source_sha256"],
                "wrong registered probe source or path")
        sources[name] = source
        expected_elfs[name] = probe["sha256"]
    bundle.write_directory(out, files)
    finish_guest(out, dict(plan, binaries=expected_elfs), study.name, TOOL_PATHS, sources=sources)
    # Extract a transferred guest tarball with --no-same-owner as the account that will run the collector.
    for program in sources:
        (out / "binaries" / program).chmod(0o700)


def main(study=HERE):
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("out", type=Path)
    cli.add_argument("--current-bundle", required=True, type=Path)
    cli.add_argument("--valgrind-deb", required=True, type=Path)
    args = cli.parse_args()
    prepare(args.out, args.current_bundle, args.valgrind_deb, study=study)


if __name__ == "__main__":
    main()
