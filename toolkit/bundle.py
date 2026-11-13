#!/usr/bin/env python3
"""Package operator-trusted builds; verify/unpack bundles without executing them.

Hashes detect inconsistent copies, not a malicious publisher. Obtain the archive
digest and source revision through a separately trusted delivery channel.
"""
import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import tarfile
import tomllib

import build_isolation
import build_runtime

ROOT = Path(__file__).resolve().parents[1]
MARKER = "durante-bundle.json"
MAX_FILE = 64 << 20
MAX_TOTAL = 256 << 20
MAX_FILES = 128
BUNDLE_DOCS = ("BUNDLE.md", "OPERATIONS.md", "ISOLATION.md", "PROTOCOL.md")
SDK_FILES = (
    ".durante-runtime.json", "License.txt", "README", "bin64/drrun",
    *build_runtime.ARTIFACTS,
    "lib64/release/libdrpreload.so", "lib64/debug/libdrpreload.so",
    "lib32/release/libdynamorio.so", "lib32/debug/libdynamorio.so",
    "ext/lib64/release/libdrmgr.so", "ext/lib64/debug/libdrmgr.so",
)
WRAPPER = b'''#!/bin/sh
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
command=${1:-doctor}
if [ "$#" -gt 0 ]; then shift; fi
case "$command" in
  run|doctor)
    exec "$root/bin/durante" "$command" --drrun "$root/sdk/bin64/drrun" --client "$root/lib/libdurante.so" "$@" ;;
  isolate)
    if [ ! -f "$root/bin/bwrap" ]; then
      echo "This bundle has no isolation launcher" >&2; exit 2
    fi
    exec "$root/bin/durante" isolate --drrun "$root/sdk/bin64/drrun" --client "$root/lib/libdurante.so" --launcher "$root/bin/bwrap" "$@" ;;
  doctor-isolate)
    if [ ! -f "$root/bin/bwrap" ]; then
      echo "This bundle has no isolation launcher" >&2; exit 2
    fi
    exec "$root/bin/durante" doctor --profile isolate --drrun "$root/sdk/bin64/drrun" --client "$root/lib/libdurante.so" --launcher "$root/bin/bwrap" "$@" ;;
  collect|--version|--help)
    exec "$root/bin/durante" "$command" "$@" ;;
  *) echo "Expected doctor, doctor-isolate, run, isolate, collect, --version or --help" >&2; exit 2 ;;
esac
'''


def fail(message):
    raise ValueError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def pairs(items):
    result = {}
    for name, value in items:
        if name in result:
            fail("duplicate JSON field")
        result[name] = value
    return result


def decode(data):
    if len(data) > 65536:
        fail("manifest exceeds 64 KiB")
    try:
        value = json.loads(data, object_pairs_hook=pairs)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise ValueError("invalid manifest") from error
    if type(value) is not dict:
        fail("manifest must be an object")
    return value


def safe_name(name):
    if (type(name) is not str or not 1 <= len(name) <= 256 or name.count("/") > 8
            or not re.fullmatch(r"[A-Za-z0-9_./-]+", name)
            or str(PurePosixPath(name)) != name or name.startswith("/")
            or any(part in ("", ".", "..") for part in name.split("/"))):
        fail("unsafe inventory or archive path")
    return name


def read_file(path, maximum=MAX_FILE, allow_source_links=False):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 and not allow_source_links
                or before.st_mode & 0o6022 or before.st_size > maximum):
            fail("unsafe, linked or oversized input file")
        data = stream.read(maximum + 1)
        after = os.fstat(stream.fileno())
        fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_mode", "st_nlink")
        if len(data) != before.st_size or any(getattr(before, key) != getattr(after, key) for key in fields):
            fail("input changed while reading")
        return data, stat.S_IMODE(before.st_mode)


def expected_names(launcher):
    names = {"bin/durante", "bin/durante-bundle", "lib/libdurante.so", "LICENSE", "SECURITY.md",
             "licenses/LICENSE.DynamoRIO"}
    names.update("docs/" + name for name in BUNDLE_DOCS)
    names.update("sdk/" + name for name in SDK_FILES)
    if launcher:
        names.update({"bin/bwrap", "bin/bwrap.durante-isolation.json", "licenses/LICENSE.bubblewrap"})
    return names


def source_snapshot():
    # These are the inputs read by packaging and the runtime/client/CLI sources
    # named by its reference recipe, not a claim of binary reproducibility.
    names = {"Cargo.toml", "Cargo.lock", "LICENSE", "README.md", "SECURITY.md",
             "toolkit/bundle.py", "toolkit/build_runtime.py", "toolkit/build_isolation.py",
             "toolkit/fetch_sdk.py", "toolkit/sdk.lock.json"}
    names.update("docs/" + name for name in BUNDLE_DOCS)
    for directory, patterns in (("src", ("*.rs",)), ("client", ("*.c", "*.h", "CMakeLists.txt")),
                                ("toolkit/patches", ("*.patch", "LICENSE.*"))):
        for pattern in patterns:
            names.update(path.relative_to(ROOT).as_posix() for path in (ROOT / directory).glob(pattern))
    return {name: digest(read_file(ROOT / name)[0]) for name in sorted(names)}


def committed_source():
    snapshot = source_snapshot()
    revision = command(["git", "-C", str(ROOT), "rev-parse", "HEAD"])
    tree = command(["git", "-C", str(ROOT), "rev-parse", "HEAD^{tree}"])
    tracked = command(["git", "-C", str(ROOT), "ls-files", "--error-unmatch", "--", *snapshot])
    dirty = command(["git", "-C", str(ROOT), "diff", "--quiet", "HEAD", "--", *snapshot])
    if revision.returncode or tree.returncode or tracked.returncode or dirty.returncode or snapshot != source_snapshot():
        fail("runtime and packaging inputs must match a committed checkout")
    return revision.stdout.decode().strip(), tree.stdout.decode().strip(), snapshot


def verify_contents(contents, revision=None):
    """Only parse bounded bytes; never load or execute a bundled executable."""
    if MARKER not in contents:
        fail("missing bundle manifest")
    manifest = decode(contents[MARKER][0])
    fields = {"schema", "kind", "version", "source_revision", "source_tree", "source_files", "recipe",
              "platform", "runtime_abi", "authority", "launcher", "reference_build_recipe", "files"}
    if (set(manifest) != fields or type(manifest["schema"]) is not int or manifest["schema"] != 1
            or type(manifest["recipe"]) is not int or manifest["recipe"] != 1
            or manifest["kind"] != "durante-runtime-bundle" or manifest["platform"] != "linux-x86_64"
            or manifest["authority"] != "diagnostic_only" or type(manifest["runtime_abi"]) is not int
            or manifest["runtime_abi"] != 1 or type(manifest["launcher"]) is not bool):
        fail("unsupported bundle schema or recipe")
    for key in ("source_revision", "source_tree"):
        if type(manifest[key]) is not str or not re.fullmatch(r"[0-9a-f]{40}", manifest[key]):
            fail("invalid source identity")
    if revision is not None and manifest["source_revision"] != revision:
        fail("bundle source revision differs from requested version")
    if manifest["source_files"] != source_snapshot():
        fail("bundle packaging/runtime sources differ from this release verifier")
    version = tomllib.loads((ROOT / "Cargo.toml").read_text())["package"]["version"]
    if manifest["version"] != version or manifest["reference_build_recipe"] != reference_recipe():
        fail("bundle version or build recipe differs from this verifier")
    names = expected_names(manifest["launcher"])
    if set(contents) != names | {MARKER} or type(manifest["files"]) is not dict or set(manifest["files"]) != names:
        fail("missing or unexpected bundle files")
    if contents[MARKER][1] != 0o644:
        fail("unsafe bundle manifest mode")
    total = 0
    for name, expected in manifest["files"].items():
        safe_name(name)
        data, mode = contents[name]
        total += len(data)
        if (type(expected) is not dict or set(expected) != {"sha256", "bytes", "mode"}
                or type(expected["bytes"]) is not int or type(expected["mode"]) is not int
                or expected != {"sha256": digest(data), "bytes": len(data), "mode": mode}
                or mode not in (0o644, 0o755) or len(data) > MAX_FILE):
            fail("bundle inventory hash, length or mode mismatch")
    if len(contents) > MAX_FILES or total > MAX_TOTAL:
        fail("bundle exceeds inventory bounds")
    for name in ("bin/durante", "bin/durante-bundle", "sdk/bin64/drrun") + (("bin/bwrap",) if manifest["launcher"] else ()):
        if contents[name][1] != 0o755:
            fail("bundle launcher is not executable")
    if contents["bin/durante-bundle"][0] != WRAPPER:
        fail("bundle entrypoint differs from reviewed recipe")
    runtime = decode(contents["sdk/.durante-runtime.json"][0])
    expected_inputs = build_runtime._inputs(build_runtime.PATCH.read_bytes(), build_runtime.REPLAY_PATCH.read_bytes())
    if (type(runtime.get("schema")) is not int or runtime["schema"] != 1
            or runtime.get("inputs") != expected_inputs or type(runtime["inputs"].get("recipe")) is not int
            or type(runtime.get("artifacts")) is not dict or set(runtime["artifacts"]) != set(build_runtime.ARTIFACTS)):
        fail("SDK differs from the pinned source and two runtime patches")
    for name in build_runtime.ARTIFACTS:
        if runtime["artifacts"][name] != digest(contents["sdk/" + name][0]):
            fail("SDK core or debug symbols differ from runtime manifest")
    if manifest["launcher"]:
        launcher = decode(contents["bin/bwrap.durante-isolation.json"][0])
        if (type(launcher.get("schema")) is not int or launcher["schema"] != 1
                or launcher.get("inputs") != build_isolation.inputs()
                or type(launcher["inputs"].get("recipe")) is not int
                or launcher.get("binary_sha256") != digest(contents["bin/bwrap"][0])):
            fail("isolation launcher differs from reviewed build recipe")
    return manifest


def reference_recipe():
    return {"sdk": "python3 toolkit/fetch_sdk.py",
            "client": ["cmake -S client -B build -DCMAKE_BUILD_TYPE=Release -DDynamoRIO_DIR=SDK/cmake",
                       "cmake --build build -j2"],
            "cli": "cargo build --locked --release",
            "optional_launcher": "python3 toolkit/build_isolation.py --output BWRAP --work-dir BUILD"}


def read_directory(root):
    contents = {}
    if not stat.S_ISDIR(root.lstat().st_mode) or root.stat().st_mode & 0o022:
        fail("bundle must be a protected real directory")
    total, entries, directories = 0, 0, set()
    def visit(directory, depth):
        nonlocal entries, total
        if depth > 8:
            fail("bundle exceeds directory-depth bound")
        # os.walk first materializes every child of a directory. Consume the
        # iterator directly so an oversized directory stops at MAX_FILES + 1.
        with os.scandir(directory) as children:
            for child in children:
                entries += 1
                if entries > MAX_FILES:
                    fail("bundle exceeds directory-entry bound")
                path = directory / child.name
                name = safe_name(path.relative_to(root).as_posix())
                info = child.stat(follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    if info.st_mode & 0o022:
                        fail("unsafe bundle directory")
                    directories.add(name)
                    visit(path, depth + 1)
                else:
                    if not stat.S_ISREG(info.st_mode):
                        fail("unsafe bundle entry or symlink")
                    if total + info.st_size > MAX_TOTAL:
                        fail("bundle exceeds inventory bounds")
                    data, mode = read_file(path)
                    total += len(data)
                    if total > MAX_TOTAL:
                        fail("bundle exceeds inventory bounds")
                    contents[name] = data, mode

    visit(root, 0)
    expected_directories = {str(parent) for name in contents for parent in PurePosixPath(name).parents if str(parent) != "."}
    if directories != expected_directories:
        fail("unexpected empty bundle directory")
    return contents


def read_archive(path, expected_digest):
    archive, _ = read_file(path, MAX_TOTAL)
    if not re.fullmatch(r"[0-9a-f]{64}", expected_digest) or digest(archive) != expected_digest:
        fail("archive SHA-256 mismatch")
    # Bound decompression before tarfile parses PAX/long-name metadata. Checking
    # only returned members would permit an oversized hidden metadata record.
    with gzip.GzipFile(fileobj=io.BytesIO(archive), mode="rb") as zipped:
        expanded = zipped.read(MAX_TOTAL + 1)
    if len(expanded) > MAX_TOTAL:
        fail("expanded archive exceeds byte bound")
    contents, total = {}, 0
    with tarfile.open(fileobj=io.BytesIO(expanded), mode="r:") as source:
        for member in source:
            name = safe_name(member.name)
            if (name in contents or member.type != tarfile.REGTYPE or member.pax_headers
                    or member.size < 0 or member.size > MAX_FILE or member.mode not in (0o644, 0o755)):
                fail("duplicate, linked, special or oversized archive member")
            total += member.size
            if len(contents) >= MAX_FILES or total > MAX_TOTAL:
                fail("archive exceeds inventory bounds")
            with source.extractfile(member) as stream:
                data = stream.read(member.size + 1)
            if len(data) != member.size:
                fail("truncated archive member")
            contents[name] = data, member.mode
    return contents


def write_directory(destination, contents):
    # mkdir and exclusive file opens deliberately refuse all previous outputs.
    destination.mkdir(mode=0o700)
    for name, (data, mode) in sorted(contents.items()):
        path = destination / safe_name(name)
        path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(data)
        path.chmod(mode)


def write_archive(path, contents):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as raw, gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as zipped:
        with tarfile.open(fileobj=zipped, mode="w", format=tarfile.USTAR_FORMAT) as archive:
            for name, (data, mode) in sorted(contents.items()):
                member = tarfile.TarInfo(safe_name(name))
                member.size, member.mode = len(data), mode
                member.uid = member.gid = member.mtime = 0
                archive.addfile(member, io.BytesIO(data))


def command(args, timeout=15):
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env={"PATH": os.defpath, "LANG": "C", "LC_ALL": "C"}, timeout=timeout)
    if len(result.stdout) > 65536 or len(result.stderr) > 65536:
        fail("trusted build tool returned oversized output")
    return result


def pack(args):
    if os.path.lexists(args.output) or os.path.lexists(args.archive):
        fail("bundle output directory and archive must both be fresh")
    if args.output.resolve() in args.archive.resolve().parents:
        fail("archive must be outside the bundle directory")
    revision, tree, source_files = committed_source()
    sdk = args.sdk.resolve(strict=True)
    cli = args.cli.resolve(strict=True)
    client = args.client.resolve(strict=True)
    # This is the only executing operation: these are explicitly operator-trusted
    # local builds, not files accepted from an archive or downloaded by this tool.
    result = command([str(cli), "doctor", "--drrun", str(sdk / "bin64/drrun"), "--client", str(client)])
    diagnostic = decode(result.stdout)
    if (result.returncode != 0 or diagnostic.get("checks_passed") is not True
            or diagnostic.get("authority") != "diagnostic_only" or diagnostic.get("integrity_verified") is not False):
        fail("trusted CLI prerequisite validation failed")
    dynamic = command(["readelf", "--dynamic", str(client)])
    if dynamic.returncode or b"(RPATH)" in dynamic.stdout or b"(RUNPATH)" in dynamic.stdout:
        fail("client must have no embedded RPATH/RUNPATH; rebuild with repository CMake defaults")
    needed = set(re.findall(rb"\(NEEDED\).*?\[([^]]+)\]", dynamic.stdout))
    if needed != {b"libdrmgr.so", b"libdynamorio.so", b"libc.so.6"}:
        fail("client dependencies differ from the reviewed runtime subset")
    symbols = command(["readelf", "--dyn-syms", "--wide", str(client)])
    imports = set(re.findall(rb"FUNC\s+GLOBAL\s+DEFAULT\s+UND\s+(\S+)", symbols.stdout))
    if symbols.returncode or not {b"dr_durante_runtime_abi_version", b"dr_register_durante_code_replay_event",
                                  b"dr_unregister_durante_code_replay_event"}.issubset(imports):
        fail("client lacks strong private replay ABI imports; rebuild SDK, client and CLI together")
    sources = {"bin/durante": cli, "lib/libdurante.so": client,
               "LICENSE": ROOT / "LICENSE", "SECURITY.md": ROOT / "SECURITY.md",
               "licenses/LICENSE.DynamoRIO": ROOT / "toolkit/patches/LICENSE.DynamoRIO"}
    sources.update({"docs/" + name: ROOT / "docs" / name for name in BUNDLE_DOCS})
    sources.update({"sdk/" + name: sdk / name for name in SDK_FILES})
    if args.launcher:
        launcher = args.launcher.resolve(strict=True)
        sources.update({"bin/bwrap": launcher,
                        "bin/bwrap.durante-isolation.json": build_isolation.manifest_path(launcher),
                        "licenses/LICENSE.bubblewrap": ROOT / "toolkit/patches/LICENSE.bubblewrap"})
        checked = command([str(cli), "doctor", "--profile", "isolate", "--drrun", str(sdk / "bin64/drrun"),
                           "--client", str(client), "--launcher", str(launcher)])
        report = decode(checked.stdout)
        if not any(row.get("id") == "isolation_launcher" and row.get("status") == "pass" for row in report.get("checks", [])):
            fail("trusted CLI rejected the isolation launcher")
    contents = {}
    for name, source in sources.items():
        # Cargo may hardlink its trusted final executable to target/deps. Copy
        # the stable bytes, never the link; installed/archive files remain unique.
        data, mode = read_file(source, allow_source_links=True)
        contents[name] = data, 0o755 if mode & 0o111 else 0o644
    contents["bin/durante-bundle"] = WRAPPER, 0o755
    manifest = {"schema": 1, "kind": "durante-runtime-bundle",
                "version": tomllib.loads((ROOT / "Cargo.toml").read_text())["package"]["version"],
                "source_revision": revision, "source_tree": tree, "source_files": source_files,
                "recipe": 1, "platform": "linux-x86_64", "runtime_abi": 1, "authority": "diagnostic_only",
                "launcher": args.launcher is not None, "reference_build_recipe": reference_recipe(),
                "files": {name: {"sha256": digest(data), "bytes": len(data), "mode": mode}
                          for name, (data, mode) in sorted(contents.items())}}
    contents[MARKER] = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode(), 0o644
    verify_contents(contents)
    # Reject replacement during snapshotting before publishing any bundle.
    for name, source in sources.items():
        if read_file(source, allow_source_links=True)[0] != contents[name][0]:
            fail("source artifact changed during packaging")
    if committed_source() != (revision, tree, source_files):
        fail("source checkout changed during packaging")
    write_directory(args.output, contents)
    verify_contents(read_directory(args.output))
    write_archive(args.archive, contents)
    return {"archive_sha256": digest(read_file(args.archive, MAX_TOTAL)[0]),
            "source_revision": manifest["source_revision"], "files": len(contents),
            "authority": "diagnostic_only"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    packing = commands.add_parser("pack", help="package operator-trusted local artifacts")
    for name in ("sdk", "cli", "client", "output", "archive"):
        packing.add_argument("--" + name, required=True, type=Path)
    packing.add_argument("--launcher", type=Path)
    for name in ("verify", "unpack"):
        sub = commands.add_parser(name, help="static verification; no bundled code is executed")
        sub.add_argument("--archive", type=Path, required=True)
        sub.add_argument("--sha256", required=True)
        sub.add_argument("--source-revision", required=True)
        if name == "unpack":
            sub.add_argument("--output", type=Path, required=True)
    directory = commands.add_parser("verify-directory", help="check inventory, not publisher authenticity")
    directory.add_argument("--directory", type=Path, required=True)
    directory.add_argument("--source-revision", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "pack":
            result = pack(args)
        else:
            contents = read_directory(args.directory) if args.command == "verify-directory" else read_archive(args.archive, args.sha256)
            manifest = verify_contents(contents, args.source_revision)
            if args.command == "unpack":
                write_directory(args.output, contents)
            result = {"verified": True, "source_revision": manifest["source_revision"],
                      "files": len(contents), "execution_tested": False, "integrity_verified": False}
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValueError, RuntimeError, EOFError, RecursionError, tarfile.TarError, subprocess.SubprocessError) as error:
        parser.exit(1, "bundle: " + str(error) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
