#!/usr/bin/env python3
"""Build and install the pinned, patched Linux x86-64 DynamoRIO runtime.

The official SDK still supplies headers, extensions and frontends. Only the two
core runtime profiles and their matching debug symbols are replaced. This is a
local cache/provenance check, not a signature against a malicious SDK owner.
"""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import stat
import subprocess
import tarfile
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parent
COMMIT = "6847bb210bea0ca11d1bf47704383d6a075da490"
SOURCE_URL = f"https://codeload.github.com/DynamoRIO/dynamorio/tar.gz/{COMMIT}"
SOURCE_BYTES = 19390061
SOURCE_SHA256 = "e2b7d3f1917ff64e644b81efd96a610a3c622f4857c75593fef5d5e581c231f6"
SOURCE_ROOT = f"dynamorio-{COMMIT}"
PATCH = ROOT / "patches" / "dynamorio-vfork-lifetime.patch"
MANIFEST = ".durante-runtime.json"
PROFILES = {"release": "Release", "debug": "Debug"}
STOCK_HASHES = {
    "lib64/release/libdynamorio.so": "9181f594aeeed921f44d572aebf429069f5f1e33a01f95b4ae32a100bf48788c",
    "lib64/debug/libdynamorio.so": "f4d06eede95e09f214b2ba1a264c2a189d89fe0463636418b8cc3f9dfbd3483e",
}
ARTIFACTS = tuple(path + suffix for path in STOCK_HASHES for suffix in ("", ".debug"))
CMAKE_OPTIONS = [
    # The upstream CMake 3.7 policy otherwise probes types in the compiler
    # default dialect (C23 on GCC 15), then builds its targets as C17.
    "-DCMAKE_POLICY_DEFAULT_CMP0067=NEW", "-DCMAKE_C_STANDARD=17",
    "-DCMAKE_C_STANDARD_REQUIRED=ON", "-DVERSION_NUMBER=11.3.0", "-DBUILD_NUMBER=1",
    *[f"-DBUILD_{part}=OFF" for part in ("EXT", "TOOLS", "DOCS", "TESTS", "SAMPLES", "CLIENTS")],
]


def _hash_file(path):
    if not stat.S_ISREG(path.lstat().st_mode):
        raise RuntimeError("runtime input or artifact is not a regular file")
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _inputs(patch):
    return {"source_commit": COMMIT, "source_sha256": SOURCE_SHA256,
            "patch_sha256": hashlib.sha256(patch).hexdigest(),
            "recipe": 1, "cmake_options": CMAKE_OPTIONS, "profiles": PROFILES}


def _json_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate manifest field")
        result[key] = value
    return result


def _read_manifest(path):
    if not stat.S_ISREG(path.lstat().st_mode) or path.stat().st_size > 65536:
        raise RuntimeError("unsafe runtime manifest")
    try:
        result = json.loads(path.read_text(), object_pairs_hook=_json_pairs)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise RuntimeError("invalid runtime manifest") from error
    if not isinstance(result, dict) or result.get("schema") != 1:
        raise RuntimeError("unsupported runtime manifest")
    inputs = result.get("inputs")
    if (not isinstance(inputs, dict) or inputs.get("source_commit") != COMMIT or
            inputs.get("source_sha256") != SOURCE_SHA256):
        raise RuntimeError("runtime manifest has an unknown source pin")
    artifacts = result.get("artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != set(ARTIFACTS):
        raise RuntimeError("runtime manifest artifact set differs")
    for name, digest in artifacts.items():
        if digest is None and name.endswith(".debug"):
            continue
        if (not isinstance(digest, str) or len(digest) != 64 or
                any(char not in "0123456789abcdef" for char in digest)):
            raise RuntimeError("invalid runtime artifact digest")
    return result


def verify_upstream_runtime(sdk):
    """Require actual stock cores for an explicitly unpatched comparison."""
    if os.path.lexists(sdk / MANIFEST):
        raise RuntimeError("upstream-only comparisons require a fresh SDK directory")
    for name, expected in STOCK_HASHES.items():
        if _hash_file(sdk / name) != expected:
            raise RuntimeError("SDK core is neither the pinned stock runtime nor a verified cache")


def _verify_sdk(sdk, inputs):
    # Do not traverse a swapped profile directory when installing libraries.
    for directory in (sdk / "lib64", *(sdk / "lib64" / p for p in PROFILES)):
        if not stat.S_ISDIR(directory.lstat().st_mode):
            raise RuntimeError("SDK runtime directory is missing or is a symlink")
    marker = sdk / MANIFEST
    if os.path.lexists(marker):
        previous = _read_manifest(marker)
        for name, expected in previous["artifacts"].items():
            path = sdk / name
            if expected is None:
                if os.path.lexists(path):
                    raise RuntimeError("unrecorded runtime debug symbols found")
            elif not path.exists() or _hash_file(path) != expected:
                raise RuntimeError("cached runtime artifact hash mismatch; restore the SDK")
        return previous if previous["inputs"] == inputs else None
    verify_upstream_runtime(sdk)
    return None


def _check_archive(path):
    if path.stat().st_size != SOURCE_BYTES or _hash_file(path) != SOURCE_SHA256:
        raise RuntimeError("source archive does not match the pinned size and SHA-256")


def _get_archive(work_dir, supplied):
    if supplied is not None:
        path = Path(supplied).absolute()
        _check_archive(path)
        return path
    path = work_dir / f"dynamorio-{COMMIT}.tar.gz"
    if path.exists():
        _check_archive(path)
        return path
    descriptor, temporary = tempfile.mkstemp(prefix="source-download-", dir=work_dir)
    temporary = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as output, urllib.request.urlopen(SOURCE_URL, timeout=120) as response:
            total = 0
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > SOURCE_BYTES:
                    raise RuntimeError("source archive exceeds the pinned download size")
                output.write(chunk)
        _check_archive(temporary)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def _extract_source(archive, destination):
    """Extract only regular files/directories, without tarfile extraction filters."""
    members = []
    names = set()
    total = 0
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar:
            path = PurePosixPath(member.name)
            if (path.is_absolute() or ".." in path.parts or not path.parts or
                    path.parts[0] != SOURCE_ROOT or member.name in names or
                    not (member.isdir() or member.isfile())):
                raise RuntimeError("unsafe or duplicate source archive member")
            if member.size < 0 or member.size > 64 * 1024**2:
                raise RuntimeError("source archive file exceeds extraction bound")
            total += member.size
            if len(members) >= 10000 or total > 256 * 1024**2:
                raise RuntimeError("source archive exceeds extraction bounds")
            names.add(member.name)
            members.append(member)
        # Validate every member before writing any of them. The caller supplies
        # a newly created directory, so no existing symlink can redirect writes.
        for member in members:
            target = destination.joinpath(*PurePosixPath(member.name).parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                target.chmod(0o755)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(member) as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output, 1024 * 1024)
                target.chmod(0o755 if member.mode & 0o111 else 0o644)
    source = destination / SOURCE_ROOT
    if not (source / "CMakeLists.txt").is_file():
        raise RuntimeError("source archive lacks the expected project")
    return source


def _command(args, *, cwd, env):
    subprocess.run(args, cwd=cwd, env=env, umask=0o022, check=True)


def _compile_profiles(source, session, jobs, env):
    built = {}
    for profile, kind in PROFILES.items():
        build = session / f"build-{profile}"
        _command(["cmake", "-S", str(source), "-B", str(build),
                  f"-DCMAKE_BUILD_TYPE={kind}", *CMAKE_OPTIONS], cwd=session, env=env)
        _command(["cmake", "--build", str(build), "--target", "dynamorio", "-j", str(jobs)],
                 cwd=session, env=env)
        relative = f"lib64/{profile}/libdynamorio.so"
        binary = build / relative
        with binary.open("rb") as stream:
            header = stream.read(20)
        if header[:6] != b"\x7fELF\x02\x01" or header[18:20] != b"\x3e\x00":
            raise RuntimeError("built runtime is not a Linux x86-64 ELF library")
        built[relative] = binary
        symbols = binary.with_name(binary.name + ".debug")
        built[relative + ".debug"] = symbols if symbols.is_file() else None
    return built


@contextmanager
def _sdk_lock(sdk):
    fd = os.open(sdk / ".durante-runtime.lock", os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise RuntimeError("unsafe runtime lock file")
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _install(sdk, built, inputs):
    """Stage both profiles first; replace files atomically and commit the manifest last.

    Each rename is atomic, not the entire set. Normal exceptions roll back. A
    killed installer leaves an absent/stale manifest, so a later build rejects
    the mixed cache instead of silently using it. Do not execute from an SDK
    while replacing it.
    """
    result = {"schema": 1, "inputs": inputs,
              "artifacts": {name: _hash_file(path) if path else None for name, path in built.items()}}
    if set(built) != set(ARTIFACTS):
        raise RuntimeError("incomplete runtime build output")
    with tempfile.TemporaryDirectory(prefix=".durante-install-", dir=sdk) as directory:
        stage = Path(directory)
        prepared = {}
        backups = {}
        for index, (name, source) in enumerate(built.items()):
            destination = sdk / name
            if os.path.lexists(destination):
                if not stat.S_ISREG(destination.lstat().st_mode):
                    raise RuntimeError("unsafe runtime replacement target")
                backup = stage / f"backup-{index}"
                os.link(destination, backup, follow_symlinks=False)
                backups[name] = backup
            if source:
                target = stage / f"new-{index}"
                shutil.copyfile(source, target)
                target.chmod(0o644 if name.endswith(".debug") else 0o755)
                if _hash_file(target) != result["artifacts"][name]:
                    raise RuntimeError("runtime build output changed during staging")
                with target.open("rb") as stream:
                    os.fsync(stream.fileno())
                prepared[name] = target
            else:
                prepared[name] = None
        marker = sdk / MANIFEST
        if os.path.lexists(marker):
            backup = stage / "backup-manifest"
            os.link(marker, backup, follow_symlinks=False)
            backups[MANIFEST] = backup
        manifest_file = stage / "new-manifest"
        manifest_file.write_text(json.dumps(result, indent=2) + "\n")
        manifest_file.chmod(0o644)
        prepared[MANIFEST] = manifest_file
        installed = []
        try:
            for name, source in prepared.items():
                if source:
                    os.replace(source, sdk / name)
                else:
                    (sdk / name).unlink(missing_ok=True)
                installed.append(name)
        except BaseException:
            for name in reversed(installed):
                if name in backups:
                    os.replace(backups[name], sdk / name)
                else:
                    (sdk / name).unlink(missing_ok=True)
            raise
    return result


def build_runtime(sdk: Path, work_dir: Path, jobs=2, source_archive=None):
    if platform.system() != "Linux" or platform.machine() not in ("x86_64", "AMD64"):
        raise RuntimeError("runtime build requires Linux x86-64")
    if type(jobs) is not int or not 1 <= jobs <= 32:
        raise RuntimeError("build jobs must be between 1 and 32")
    sdk = Path(sdk).resolve(strict=True)
    work_dir = Path(work_dir).absolute()
    work_dir.mkdir(parents=True, exist_ok=True)
    patch = PATCH.read_bytes()
    inputs = _inputs(patch)
    with _sdk_lock(sdk):
        cached = _verify_sdk(sdk, inputs)
        if source_archive is not None:
            _check_archive(Path(source_archive))
        if cached is not None:
            print("runtime_cache=verified", flush=True)
            return cached
        archive = _get_archive(work_dir, source_archive)
        with tempfile.TemporaryDirectory(prefix="runtime-build-", dir=work_dir) as directory:
            session = Path(directory)
            # Copy before extracting so the checked input cannot change under a
            # concurrent replacement of the user's archive path.
            snapshot = session / "source.tar.gz"
            shutil.copyfile(archive, snapshot)
            _check_archive(snapshot)
            source = _extract_source(snapshot, session)
            patch_file = session / "runtime.patch"
            patch_file.write_bytes(patch)
            temporary = session / "tmp"
            temporary.mkdir()
            env = os.environ.copy()
            env["TMPDIR"] = str(temporary)
            for key in ("CFLAGS", "CXXFLAGS", "CPPFLAGS", "LDFLAGS"):
                env.pop(key, None)
            _command(["patch", "--batch", "--forward", "--fuzz=0", "-p1", "-i", str(patch_file)],
                     cwd=source, env=env)
            built = _compile_profiles(source, session, jobs, env)
            # Verify the installed input again before changing any artifact.
            _verify_sdk(sdk, inputs)
            result = _install(sdk, built, inputs)
        print("runtime_build=installed; profiles=release,debug", flush=True)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk", required=True, type=Path)
    parser.add_argument("--work-dir", required=True, type=Path)
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--source-archive", type=Path)
    args = parser.parse_args()
    try:
        build_runtime(args.sdk, args.work_dir, args.jobs, args.source_archive)
    except subprocess.CalledProcessError as error:
        raise SystemExit(f"runtime build command failed (exit {error.returncode})") from error
    except (OSError, RuntimeError, tarfile.TarError) as error:
        raise SystemExit(f"runtime build failed: {error}") from error


if __name__ == "__main__":
    main()
