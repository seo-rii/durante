"""Untrusted archive/inventory parsing, distinct from the real relocated smoke."""
import gzip
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "toolkit"))
SPEC = importlib.util.spec_from_file_location("durante_bundle", ROOT / "toolkit/bundle.py")
bundle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bundle)


def encode(value):
    return (json.dumps(value, sort_keys=True) + "\n").encode()


def inventory(contents):
    manifest = bundle.decode(contents[bundle.MARKER][0])
    manifest["files"] = {name: {"sha256": bundle.digest(data), "bytes": len(data), "mode": mode}
                         for name, (data, mode) in contents.items() if name != bundle.MARKER}
    contents[bundle.MARKER] = encode(manifest), 0o644
    return contents


def fixture(launcher=False):
    contents = {name: (b"non-executable fixture bytes\n", 0o644) for name in bundle.expected_names(launcher)}
    for name in ("bin/durante", "sdk/bin64/drrun") + (("bin/bwrap",) if launcher else ()):
        contents[name] = b"#!/bin/sh\nexit 99\n", 0o755
    contents["bin/durante-bundle"] = bundle.WRAPPER, 0o755
    runtime = {"schema": 1, "inputs": bundle.build_runtime._inputs(bundle.build_runtime.PATCH.read_bytes()),
               "artifacts": {name: bundle.digest(contents["sdk/" + name][0]) for name in bundle.build_runtime.ARTIFACTS}}
    contents["sdk/.durante-runtime.json"] = encode(runtime), 0o644
    if launcher:
        contents["bin/bwrap.durante-isolation.json"] = encode({"schema": 1,
            "inputs": bundle.build_isolation.inputs(), "binary_sha256": bundle.digest(contents["bin/bwrap"][0])}), 0o644
    manifest = {"schema": 1, "kind": "durante-runtime-bundle", "version": "0.1.0",
                "source_revision": "a" * 40, "source_tree": "b" * 40, "recipe": 1,
                "source_files": bundle.source_snapshot(),
                "platform": "linux-x86_64", "runtime_abi": 1, "authority": "diagnostic_only",
                "launcher": launcher, "reference_build_recipe": bundle.reference_recipe(), "files": {}}
    contents[bundle.MARKER] = encode(manifest), 0o644
    return inventory(contents)


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="durante-bundle-unit-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def archive(self, contents):
        path = self.root / "bundle.tar.gz"
        bundle.write_archive(path, contents)
        return path, bundle.digest(path.read_bytes())

    def test_static_roundtrip_and_unpack_do_not_execute_bundled_files(self):
        contents = fixture(True)
        archive, digest = self.archive(contents)
        with patch.object(bundle, "command", side_effect=AssertionError("verification executed a command")):
            restored = bundle.read_archive(archive, digest)
            self.assertEqual(bundle.verify_contents(restored, "a" * 40)["runtime_abi"], 1)
            destination = self.root / "installed"
            bundle.write_directory(destination, restored)
            self.assertEqual(bundle.read_directory(destination), contents)
            bundle.verify_contents(bundle.read_directory(destination), "a" * 40)

    def test_archive_and_source_identity_are_external_expectations(self):
        archive, _ = self.archive(fixture())
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            bundle.read_archive(archive, "0" * 64)
        with self.assertRaisesRegex(ValueError, "source revision"):
            bundle.verify_contents(fixture(), "c" * 40)
        contents = fixture()
        manifest = bundle.decode(contents[bundle.MARKER][0])
        manifest["source_files"]["toolkit/bundle.py"] = "0" * 64
        contents[bundle.MARKER] = encode(manifest), 0o644
        with self.assertRaisesRegex(ValueError, "release verifier"):
            bundle.verify_contents(contents)

    def test_pack_requires_committed_packaging_and_runtime_inputs(self):
        checkout = self.root / "checkout"
        for name in set(bundle.source_snapshot()) | {"toolkit/fetch_sdk.py"}:
            destination = checkout / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, destination)
        def git(*args):
            subprocess.run(["git", "-C", str(checkout), *args], check=True, capture_output=True)
        git("init", "-q")
        git("config", "user.name", "Bundle test")
        git("config", "user.email", "bundle-test@example.invalid")
        git("add", ".")
        git("-c", "commit.gpgsign=false", "commit", "-qm", "test fixture")
        with patch.object(bundle, "ROOT", checkout):
            bundle.committed_source()
            for name in ("toolkit/bundle.py", "docs/BUNDLE.md", "toolkit/fetch_sdk.py"):
                path = checkout / name
                original = path.read_bytes()
                path.write_bytes(original + b"\nchanged\n")
                with self.assertRaisesRegex(ValueError, "committed checkout"):
                    bundle.committed_source()
                path.write_bytes(original)
            (checkout / "src/untracked.rs").write_text("// extra compiler input\n")
            with self.assertRaisesRegex(ValueError, "committed checkout"):
                bundle.committed_source()

    def test_mixed_missing_extra_or_modified_files_fail(self):
        for operation in ("missing", "extra", "modified", "mode"):
            with self.subTest(operation=operation):
                contents = fixture()
                if operation == "missing":
                    del contents["lib/libdurante.so"]
                elif operation == "extra":
                    contents["private.txt"] = b"private", 0o644
                elif operation == "modified":
                    contents["bin/durante"] = b"another release", 0o755
                else:
                    contents["bin/durante"] = contents["bin/durante"][0], 0o777
                with self.assertRaises(ValueError):
                    bundle.verify_contents(contents)

    def test_changed_runtime_pins_fail_even_with_updated_bundle_inventory(self):
        for key in ("source_commit", "source_sha256", "patch_sha256", "code_replay_patch_sha256", "recipe"):
            with self.subTest(key=key):
                contents = fixture()
                runtime = bundle.decode(contents["sdk/.durante-runtime.json"][0])
                runtime["inputs"][key] = 2 if key == "recipe" else "0" * 64
                contents["sdk/.durante-runtime.json"] = encode(runtime), 0o644
                with self.assertRaisesRegex(ValueError, "two runtime patches"):
                    bundle.verify_contents(inventory(contents))
        contents = fixture()
        contents["sdk/lib64/release/libdynamorio.so"] = b"wrong SDK", 0o644
        with self.assertRaisesRegex(ValueError, "runtime manifest"):
            bundle.verify_contents(inventory(contents))

    def test_optional_launcher_requires_both_reviewed_manifest_and_binary(self):
        for change in ("binary", "patch", "missing"):
            with self.subTest(change=change):
                contents = fixture(True)
                if change == "binary":
                    contents["bin/bwrap"] = b"stock bwrap", 0o755
                elif change == "missing":
                    del contents["bin/bwrap.durante-isolation.json"]
                else:
                    manifest = bundle.decode(contents["bin/bwrap.durante-isolation.json"][0])
                    manifest["inputs"]["patch_sha256"] = "0" * 64
                    contents["bin/bwrap.durante-isolation.json"] = encode(manifest), 0o644
                with self.assertRaises(ValueError):
                    bundle.verify_contents(inventory(contents))

    def test_duplicate_manifest_keys_and_bool_numbers_fail(self):
        contents = fixture()
        contents[bundle.MARKER] = b'{"schema":1,"schema":1}', 0o644
        with self.assertRaises(ValueError):
            bundle.verify_contents(contents)
        for key in ("schema", "recipe", "runtime_abi"):
            contents = fixture()
            manifest = bundle.decode(contents[bundle.MARKER][0])
            manifest[key] = True
            contents[bundle.MARKER] = encode(manifest), 0o644
            with self.assertRaises(ValueError):
                bundle.verify_contents(contents)

    def test_archive_traversal_symlink_hardlink_device_and_duplicate_fail(self):
        for case in ("../outside", "/outside", "symlink", "hardlink", "device", "duplicate"):
            with self.subTest(case=case):
                path = self.root / (case.replace("/", "_") + ".tar.gz")
                with tarfile.open(path, "w:gz") as archive:
                    member = tarfile.TarInfo(case if case.startswith(("/", ".")) else "file")
                    member.mode = 0o644
                    if case in ("symlink", "hardlink"):
                        member.type = tarfile.SYMTYPE if case == "symlink" else tarfile.LNKTYPE
                        member.linkname = "/outside"
                    elif case == "device":
                        member.type = tarfile.CHRTYPE
                    archive.addfile(member, io.BytesIO())
                    if case == "duplicate":
                        archive.addfile(member, io.BytesIO())
                with self.assertRaises(ValueError):
                    bundle.read_archive(path, bundle.digest(path.read_bytes()))
        for name in ("a/" * 10000 + "file", "x" * 257, "a/./file", "a//file"):
            with self.assertRaisesRegex(ValueError, "unsafe"):
                bundle.safe_name(name)

    def test_expansion_is_bounded_before_tar_metadata_is_parsed(self):
        path = self.root / "bomb.gz"
        header = tarfile.TarInfo("metadata")
        header.type, header.size = tarfile.XHDTYPE, 4096
        path.write_bytes(gzip.compress(header.tobuf() + b"0" * 4096))
        with patch.object(bundle, "MAX_TOTAL", 1024), self.assertRaisesRegex(ValueError, "expanded archive"):
            bundle.read_archive(path, bundle.digest(path.read_bytes()))

    def test_directory_budget_stops_before_materializing_an_oversized_listing(self):
        probe = self.root / "metadata"
        probe.write_bytes(b"x")
        info = probe.stat()

        class OversizedDirectory:
            consumed = 0
            closed = False

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.closed = True

            def __iter__(self):
                return self

            def __next__(self):
                self.consumed += 1
                if self.consumed > bundle.MAX_FILES + 1:
                    raise AssertionError("directory enumeration continued past the entry bound")
                return SimpleNamespace(name=f"file-{self.consumed}", is_dir=lambda: False,
                                       stat=lambda **kwargs: info)

        iterator = OversizedDirectory()
        with patch.object(bundle.os, "scandir", return_value=iterator), \
                patch.object(bundle, "read_file", return_value=(b"x", 0o644)) as reads, \
                self.assertRaisesRegex(ValueError, "entry bound"):
            bundle.read_directory(self.root)
        self.assertEqual(iterator.consumed, bundle.MAX_FILES + 1)
        self.assertTrue(iterator.closed)
        self.assertEqual(reads.call_count, bundle.MAX_FILES)

    def test_existing_destinations_symlinks_hardlinks_and_empty_directories_fail(self):
        contents = fixture()
        destination = self.root / "installed"
        bundle.write_directory(destination, contents)
        with self.assertRaises(FileExistsError):
            bundle.write_directory(destination, contents)
        archive, _ = self.archive(contents)
        with self.assertRaises(FileExistsError):
            bundle.write_archive(archive, contents)
        extra = destination / "empty"
        extra.mkdir()
        with self.assertRaisesRegex(ValueError, "empty"):
            bundle.read_directory(destination)
        extra.rmdir()
        nested = destination
        for _ in range(10):
            nested = nested / "deep"
            nested.mkdir()
        with self.assertRaisesRegex(ValueError, "depth"):
            bundle.read_directory(destination)
        shutil.rmtree(destination / "deep")
        for index in range(bundle.MAX_FILES):
            (destination / f"empty{index}").mkdir()
        with self.assertRaisesRegex(ValueError, "entry bound"):
            bundle.read_directory(destination)
        for path in destination.glob("empty*"):
            path.rmdir()
        library = destination / "lib/libdurante.so"
        moved = self.root / "library"
        library.rename(moved)
        library.symlink_to(moved)
        with self.assertRaisesRegex(ValueError, "symlink"):
            bundle.read_directory(destination)
        library.unlink()
        os.link(moved, library)
        with self.assertRaisesRegex(ValueError, "linked"):
            bundle.read_directory(destination)
        # Cargo's trusted source artifact may have a target/deps hardlink;
        # packaging copies its checked bytes into an independent regular file.
        self.assertEqual(bundle.read_file(moved, allow_source_links=True)[0], b"non-executable fixture bytes\n")


if __name__ == "__main__":
    unittest.main()
