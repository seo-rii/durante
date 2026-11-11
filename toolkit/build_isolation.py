#!/usr/bin/env python3
"""Build the source-pinned, non-setuid Bubblewrap with a protected PID 1.

No host installation or package mutation is performed. Meson, Ninja, a C
compiler, patch, pkg-config and libcap development files are build prerequisites.
The output manifest is consistency evidence for an operator-owned installation,
not a signature against a malicious host administrator.
"""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import shutil
import stat
import subprocess
import tarfile
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parent
VERSION = "0.12.0"
SOURCE_ROOT = "bubblewrap-" + VERSION
SOURCE_URL = f"https://github.com/containers/bubblewrap/releases/download/v{VERSION}/{SOURCE_ROOT}.tar.xz"
SOURCE_BYTES = 126452
SOURCE_SHA256 = "9760d007363e3abba7c747489910f9f82d9fca53ba3bd3282e396fa3c97a3314"
PATCH = ROOT / "patches/bubblewrap-init-guard.patch"
PATCH_SHA256 = "624bf41de9308836fed6434a656ea54c8aae4765e710ee557f95330523a5eb97"
OPTIONS = ["--buildtype=release", "-Dtests=false", "-Dman=disabled",
           "-Dselinux=disabled", "-Dbash_completion=disabled", "-Dzsh_completion=disabled"]


def digest(path):
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError("input or output is not a regular file")
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def inputs():
    return {"version": VERSION, "source_url": SOURCE_URL,
            "source_sha256": SOURCE_SHA256, "patch_sha256": PATCH_SHA256,
            "recipe": 1, "meson_options": OPTIONS}


def pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate manifest field")
        result[key] = value
    return result


def manifest_path(output):
    return output.with_name(output.name + ".durante-isolation.json")


def cached(output):
    marker = manifest_path(output)
    if not os.path.lexists(output) and not os.path.lexists(marker):
        return None
    if (not output.exists() or not marker.exists() or
            not stat.S_ISREG(marker.lstat().st_mode) or marker.stat().st_mode & 0o022 or
            marker.stat().st_size > 65536):
        raise ValueError("incomplete or unsafe isolation installation")
    info = output.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_mode & 0o6022 or
            not info.st_mode & 0o111 or not 0 < info.st_size <= 16 << 20):
        raise ValueError("unsafe isolation executable permissions")
    result = json.loads(marker.read_text(), object_pairs_hook=pairs)
    if (type(result) is not dict or type(result.get("schema")) is not int or
            result["schema"] != 1 or result.get("inputs") != inputs() or
            type(result["inputs"].get("recipe")) is not int):
        raise ValueError("isolation build recipe differs; select a fresh output path")
    if result.get("binary_sha256") != digest(output):
        raise ValueError("isolation executable hash differs from manifest")
    return result


def check_archive(path):
    if path.stat().st_size != SOURCE_BYTES or digest(path) != SOURCE_SHA256:
        raise ValueError("Bubblewrap archive does not match its pinned size and SHA-256")


def get_archive(work, supplied):
    if supplied:
        path = supplied.absolute()
        check_archive(path)
        return path
    path = work / (SOURCE_ROOT + ".tar.xz")
    if path.exists():
        check_archive(path)
        return path
    fd, name = tempfile.mkstemp(prefix="isolation-download-", dir=work)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream, urllib.request.urlopen(SOURCE_URL, timeout=120) as response:
            total = 0
            while data := response.read(65536):
                total += len(data)
                if total > SOURCE_BYTES:
                    raise ValueError("source download exceeds pinned size")
                stream.write(data)
        check_archive(temporary)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def extract(archive, destination):
    # Validate all members first. Only directories/regular source files are
    # materialized; the two upstream license aliases are unnecessary to build.
    with tarfile.open(archive, "r:xz") as tar:
        members, names, total = [], set(), 0
        for member in tar:
            path = PurePosixPath(member.name)
            if (path.is_absolute() or ".." in path.parts or not path.parts or
                    path.parts[0] != SOURCE_ROOT or member.name in names):
                raise ValueError("unsafe source archive path")
            if member.issym():
                target = posixpath.normpath(posixpath.join(str(path.parent), member.linkname))
                if not target.startswith(SOURCE_ROOT + "/"):
                    raise ValueError("unsafe source archive link")
            elif not (member.isdir() or member.isfile()):
                raise ValueError("unsupported source archive entry")
            if member.size < 0 or member.size > 4 << 20:
                raise ValueError("oversized source member")
            total += member.size
            if len(members) >= 2048 or total > 16 << 20:
                raise ValueError("source archive exceeds extraction bounds")
            names.add(member.name)
            members.append(member)
        for member in members:
            target = destination.joinpath(*PurePosixPath(member.name).parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                target.chmod(0o755)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(member) as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output)
                target.chmod(0o755 if member.mode & 0o111 else 0o644)
    return destination / SOURCE_ROOT


def build(output, work, archive=None, jobs=2, meson="meson"):
    if type(jobs) is not int or not 1 <= jobs <= 16:
        raise ValueError("jobs must be between 1 and 16")
    if digest(PATCH) != PATCH_SHA256:
        raise ValueError("isolation guard patch differs from the reviewed pin")
    output = output.absolute()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.parent.is_symlink() or output.parent.stat().st_mode & 0o022:
        raise ValueError("isolation output directory must be operator-owned and not writable by others")
    with installation_lock(output):
        return build_locked(output, work, archive, jobs, meson)


@contextmanager
def installation_lock(output):
    path = output.with_name(output.name + ".durante-isolation.lock")
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            raise ValueError("unsafe isolation installation lock")
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def build_locked(output, work, archive, jobs, meson):
    previous = cached(output)
    if previous is not None:
        print("isolation_cache=verified", flush=True)
        return previous
    work = work.absolute()
    work.mkdir(parents=True, exist_ok=True)
    archive = get_archive(work, archive)
    with tempfile.TemporaryDirectory(prefix="isolation-build-", dir=work) as directory:
        session = Path(directory)
        snapshot = session / "source.tar.xz"
        shutil.copyfile(archive, snapshot)
        check_archive(snapshot)
        source = extract(snapshot, session)
        patch = session / "init-guard.patch"
        patch.write_bytes(PATCH.read_bytes())
        if digest(patch) != PATCH_SHA256:
            raise ValueError("guard patch changed during build preparation")
        subprocess.run(["patch", "--batch", "--fuzz=0", "-p1", "-i", str(patch)], cwd=source, check=True)
        destination = session / "build"
        subprocess.run([meson, "setup", str(destination), str(source), *OPTIONS], check=True)
        subprocess.run(["ninja", "-C", str(destination), "-j", str(jobs), "bwrap"], check=True)
        binary = destination / "bwrap"
        version = subprocess.run([str(binary), "--version"], check=True, capture_output=True, text=True)
        if version.stdout.strip() != "bubblewrap " + VERSION:
            raise ValueError("unexpected isolation executable version")
        result = {"schema": 1, "inputs": inputs(), "binary_sha256": digest(binary)}
        # Stage the pair; manifest last makes interruption fail closed on reuse.
        with tempfile.TemporaryDirectory(prefix=".isolation-install-", dir=output.parent) as name:
            stage = Path(name)
            executable = stage / "bwrap"
            shutil.copyfile(binary, executable)
            executable.chmod(0o755)
            marker = stage / "manifest.json"
            marker.write_text(json.dumps(result, indent=2) + "\n")
            marker.chmod(0o644)
            installed = False
            try:
                os.replace(executable, output)
                installed = True
                os.replace(marker, manifest_path(output))
            except BaseException:
                if installed:
                    output.unlink(missing_ok=True)
                raise
    print("isolation_build=installed; version=" + VERSION, flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--work-dir", required=True, type=Path)
    parser.add_argument("--source-archive", type=Path)
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--meson", default="meson", help="trusted Meson executable")
    args = parser.parse_args()
    try:
        build(args.output, args.work_dir, args.source_archive, args.jobs, args.meson)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        parser.exit(1, "isolation build failed: " + str(error) + "\n")


if __name__ == "__main__":
    main()
