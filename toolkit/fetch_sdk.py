#!/usr/bin/env python3
"""Fetch the pinned SDK and build its corrected process-lifetime runtime."""
import argparse
import hashlib
import json
from pathlib import Path
import posixpath
import re
import shutil
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parent
TAG = "release_11.3.0-1"
COMMIT = "6847bb210bea0ca11d1bf47704383d6a075da490"
NAME = "DynamoRIO-Linux-11.3.0.tar.gz"
URL = f"https://github.com/DynamoRIO/dynamorio/releases/download/{TAG}/{NAME}"
EXPECTED_BYTES = 428589887
EXPECTED_SHA256 = "ae02049df5c4daeb82d4087aaa0d38bf58cdd26b38fb6042dccf27cbc32ceed9"

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream-only", action="store_true",
                        help="extract the unpatched runtime for diagnostic comparisons only")
    args = parser.parse_args()
    archive = ROOT / NAME
    if not archive.exists():
        if shutil.disk_usage(ROOT).free < 3 * 1024**3:
            raise SystemExit("Need at least 3 GiB free for the SDK download/extraction")
        partial = archive.with_suffix(archive.suffix + ".part")
        with urllib.request.urlopen(URL, timeout=120) as response, partial.open("wb") as out:
            shutil.copyfileobj(response, out, length=1024 * 1024)
        partial.rename(archive)
    if archive.stat().st_size != EXPECTED_BYTES:
        raise SystemExit("SDK asset size does not match the pinned GitHub release")
    digest = hashlib.file_digest(archive.open("rb"), "sha256").hexdigest()
    if digest != EXPECTED_SHA256:
        raise SystemExit("SDK SHA-256 mismatch")
    selected = ("bin64/", "lib64/", "include/", "cmake/", "ext/lib64/",
                "ext/include/", "samples/")
    # drrun validates both architectures even when only its 64-bit frontend is
    # used. These two core libraries avoid a misleading incomplete-SDK warning.
    frontend_validation = {"lib32/debug/libdynamorio.so", "lib32/release/libdynamorio.so"}
    with tarfile.open(archive, "r:gz") as tar:
        members = []
        roots = set()
        entries = tar.getmembers()
        # The official CMake config validates all exported 64-bit targets,
        # including tool libraries that this client does not itself link.
        # Include precisely those dependencies instead of extracting all tools.
        cmake_refs = set()
        for entry in entries:
            if "/cmake/" in entry.name and "64" in entry.name and entry.name.endswith(".cmake"):
                source = tar.extractfile(entry).read().decode()
                cmake_refs.update(re.findall(r'\$\{_IMPORT_PREFIX\}/([^";]+)', source))
        for entry in entries:
            parts = entry.name.split("/", 1)
            roots.add(parts[0])
            relative = parts[1] if len(parts) > 1 else ""
            if (not relative or relative.startswith(selected) or relative in cmake_refs or
                    relative in frontend_validation or
                    relative in ("License.txt", "README", "README.md")):
                members.append(entry)
        if len(roots) != 1:
            raise SystemExit(f"Unexpected archive roots: {sorted(roots)}")
        archive_root = next(iter(roots))
        for entry in members:
            name = posixpath.normpath(entry.name)
            if name.startswith("/") or not (name == archive_root or name.startswith(archive_root + "/")):
                raise SystemExit("Unsafe SDK archive path")
            if entry.issym() or entry.islnk():
                target = entry.linkname if entry.islnk() else posixpath.join(posixpath.dirname(name), entry.linkname)
                target = posixpath.normpath(target)
                if not target.startswith(archive_root + "/"):
                    raise SystemExit("Unsafe SDK archive link")
            elif not entry.isfile() and not entry.isdir():
                raise SystemExit("Unexpected SDK archive member type")
        sdk_root = ROOT / "sdk" / next(iter(roots))
        extracted_bytes = sum(m.size for m in members if m.isfile())
        print(f"sha256={digest}; selected_bytes={extracted_bytes}", flush=True)
        missing_members = [m for m in members if not (ROOT / "sdk" / m.name).exists()]
        if missing_members:
            # Explicit validation above also supports Python 3.11 before tar
            # extraction filters were backported. The full asset is hash-pinned.
            tar.extractall(ROOT / "sdk", members=missing_members)
    metadata = {
        "project": "DynamoRIO/dynamorio", "tag": TAG, "source_commit": COMMIT,
        "asset": NAME, "url": URL, "bytes": EXPECTED_BYTES, "sha256": digest,
        "github_asset_id": 219577151, "sdk_directory": str(sdk_root.relative_to(ROOT)),
        "selected_prefixes": list(selected), "selected_uncompressed_bytes": extracted_bytes,
        "cmake_imported_files": sorted(cmake_refs),
        "frontend_validation_files": sorted(frontend_validation),
        "digest_origin": "Computed from official GitHub HTTPS asset; API digest unavailable",
    }
    (ROOT / "sdk.lock.json").write_text(json.dumps(metadata, indent=2) + "\n")
    from build_runtime import build_runtime, verify_upstream_runtime
    if args.upstream_only:
        verify_upstream_runtime(sdk_root)
    else:
        build_runtime(sdk_root, ROOT / ".sdk" / "runtime")
    print(f"sdk_directory={sdk_root}", flush=True)

if __name__ == "__main__":
    main()
