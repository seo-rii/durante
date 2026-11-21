"""Run archived-evidence verifiers against the sources a frozen study registered.

A study protocol pins sha256 digests of its analysis code and of the client
sources used for collection. The archived dataset is bound to the client builds
through the protocol's tool digests, so later client changes do not alter that
evidence. When only registered ``client/`` files differ from the working tree,
this helper materializes a private copy of the tracked tree whose ``client/``
files are the registered blobs from Git history. The verifier then runs the
registered code exactly; every other registered file must still match live.
"""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
GIT_TIMEOUT = 60


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _git(*args):
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True,
                          timeout=GIT_TIMEOUT, check=True).stdout


def _registered_blob(path, digest):
    """Return the bytes of a historical revision of path with this digest."""
    for commit in _git("log", "--format=%H", "--", path).decode().split():
        try:
            blob = _git("show", f"{commit}:{path}")
        except subprocess.CalledProcessError:
            continue  # The commit removed the path.
        if _sha(blob) == digest:
            return blob
    return None


def registered_root(test, protocol):
    """Return a repository root whose registered sources match ``protocol``."""
    sources = json.loads((ROOT / protocol).read_bytes())["sources_sha256"]
    drifted = {path: digest for path, digest in sources.items()
               if not (ROOT / path).is_file() or _sha((ROOT / path).read_bytes()) != digest}
    if not drifted or any(not path.startswith("client/") for path in drifted):
        return ROOT  # Unchanged, or the live verifier must report non-client drift.
    if shutil.which("git") is None:
        raise unittest.SkipTest("registered client sources require Git history")
    try:
        tracked = _git("ls-files", "-z").split(b"\0")
        blobs = {path: _registered_blob(path, digest) for path, digest in drifted.items()}
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise unittest.SkipTest(f"registered client sources unavailable: {error}")
    missing = sorted(path for path, blob in blobs.items() if blob is None)
    if missing:
        raise unittest.SkipTest("registered client sources are not in this checkout's history "
                                "(fetch full history): " + ", ".join(missing))
    temporary = tempfile.TemporaryDirectory(prefix="durante-registered-")
    test.addCleanup(temporary.cleanup)
    root = Path(temporary.name)
    for name in tracked:
        if not name:
            continue
        source = ROOT / name.decode()
        if source.is_file() and not source.is_symlink():
            destination = root / name.decode()
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
    for path, blob in blobs.items():
        (root / path).write_bytes(blob)
    return root
