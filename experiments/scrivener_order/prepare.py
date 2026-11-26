"""Build a CPU-pinned Scrivener study guest from the registered Durante release archives."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "toolkit"))
sys.path.insert(0, str(HERE.parent))
import bundle
from calibrated_limits.prepare import digest
from cpp20_locality.verify import require
from scrivener_order.bench import TOOL_PATHS


def release_files(archive, expected, client_path, *, include_sdk):
    contents = bundle.read_archive(archive, expected["sha256"])
    manifest = bundle.decode(contents[bundle.MARKER][0])
    require(manifest["source_revision"] == expected["revision"], "wrong release revision")
    files = {client_path: contents["lib/libdurante.so"]}
    if include_sdk:
        for name in bundle.SDK_FILES:
            files["dynamorio/" + name] = contents["sdk/" + name]
    return files


def prepare(out, previous_bundle, current_bundle):
    out = out.resolve()
    require(not out.is_relative_to(HERE.parent), "choose a guest directory outside the experiment sources")
    plan = json.loads((HERE / "plan.json").read_bytes())
    # Read the two bound archives before creating the guest. The operator obtains
    # their expected digests and revisions from the committed plan.
    files = {}
    for arm, archive in (("previous", previous_bundle), ("current", current_bundle)):
        files.update(release_files(archive, plan["bundles"][arm], TOOL_PATHS[arm], include_sdk=arm == "current"))
    bundle.write_directory(out, files)
    finish_guest(out, plan, HERE.name, TOOL_PATHS)


def finish_guest(out, plan, study, tool_paths):
    # The guest's collectors and corpus generators use repository-relative imports.
    shutil.copytree(HERE.parent, out / "repo/experiments",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "results"))
    shutil.copyfile(ROOT / "collect.py", out / tool_paths["reader"])
    require({key: digest(out / path) for key, path in tool_paths.items()} == plan["tools"],
            "release tools or reader differ from registration")
    binaries = out / "binaries"
    binaries.mkdir()
    sources = {"scrivener": HERE.parent / "contest_warm/scrivener.cpp",
               "scrivener_lifting": HERE.parent / "contest_submissions/scrivener_lifting.cpp"}
    require(sources.keys() == plan["binaries"].keys(), "different registered program set")
    for name, source in sources.items():
        binary = binaries / name
        subprocess.run(["g++", "-std=c++20", "-O2", "-static", "-s", "-march=x86-64-v2",
                        "-Wl,--build-id=none", source, "-o", binary], check=True)
        require(digest(binary) == plan["binaries"][name], "different static ELF: " + name)

    guest = (HERE.parent / "cpp20_locality/guest.sh").read_text()
    command = '"$PWD/bench.py"'
    require(guest.count(command) == 1, "different guest trace wrapper")
    entry = f'"$PWD/repo/experiments/{study}/bench.py" --payload "$PWD" --out "$PWD/output"'
    (out / "guest.sh").write_text(guest.replace(command, entry))
    (out / "guest.sh").chmod(0o700)
    # A failed or incomplete build has no checksum manifest and cannot start a guest run.
    paths = sorted(path for path in out.rglob("*") if path.is_file())
    (out / "checksums.sha256").write_text("".join(f"{digest(path)}  {path.relative_to(out)}\n" for path in paths))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path)
    parser.add_argument("--previous-bundle", required=True, type=Path)
    parser.add_argument("--current-bundle", required=True, type=Path)
    args = parser.parse_args()
    prepare(args.out, args.previous_bundle, args.current_bundle)
