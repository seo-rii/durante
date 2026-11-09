"""Pinned input, cache-integrity and transactional runtime installation tests."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import tarfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("runtime_builder", ROOT / "toolkit/build_runtime.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def digest(data):
    return hashlib.sha256(data).hexdigest()


class RuntimeBuildTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.base = Path(self.directory.name)
        self.sdk = self.base / "sdk"
        self.sdk.mkdir()
        self.stock = {}
        for index, name in enumerate(builder.ARTIFACTS):
            path = self.sdk / name
            path.parent.mkdir(parents=True, exist_ok=True)
            data = f"stock-{index}".encode()
            path.write_bytes(data)
            path.chmod(0o444)
            self.stock[name] = data
        self.stock_patch = mock.patch.dict(builder.STOCK_HASHES, {
            name: digest(self.stock[name]) for name in builder.STOCK_HASHES
        })
        self.stock_patch.start()
        self.addCleanup(self.stock_patch.stop)
        self.inputs = builder._inputs(b"fixed-test-patch")

    def built(self, symbols=True):
        result = {}
        for index, name in enumerate(builder.ARTIFACTS):
            if not symbols and name.endswith(".debug"):
                result[name] = None
            else:
                path = self.base / f"built-{index}"
                path.write_bytes(f"patched-{index}".encode())
                result[name] = path
        return result

    def archive(self, members):
        archive = self.base / "source.tar.gz"
        with tarfile.open(archive, "w:gz") as output:
            for name, kind, data in members:
                entry = tarfile.TarInfo(name)
                entry.type = kind
                entry.mode = 0o6777
                if kind == tarfile.REGTYPE:
                    entry.size = len(data)
                    output.addfile(entry, io.BytesIO(data))
                else:
                    entry.linkname = "../../outside"
                    output.addfile(entry)
        return archive

    def test_unknown_or_tampered_stock_sdk_is_not_overwritten(self):
        path = self.sdk / next(iter(builder.STOCK_HASHES))
        path.chmod(0o644)
        path.write_bytes(b"foreign-runtime")
        with mock.patch.object(builder, "_get_archive") as download:
            with self.assertRaisesRegex(RuntimeError, "neither the pinned stock"):
                builder.build_runtime(self.sdk, self.base / "work")
            download.assert_not_called()
        self.assertEqual(path.read_bytes(), b"foreign-runtime")

    def test_corrupt_source_archive_is_rejected_before_extraction(self):
        archive = self.base / "bad.tar.gz"
        archive.write_bytes(b"not-the-pinned-source")
        with mock.patch.object(builder, "_extract_source") as extract:
            with self.assertRaisesRegex(RuntimeError, "pinned size and SHA-256"):
                builder.build_runtime(self.sdk, self.base / "work", source_archive=archive)
            extract.assert_not_called()
        self.assertFalse((self.sdk / builder.MANIFEST).exists())

    def test_upstream_comparison_rejects_marker_and_unmarked_modified_cores(self):
        builder.verify_upstream_runtime(self.sdk)
        marker = self.sdk / builder.MANIFEST
        marker.symlink_to(self.sdk / "missing-manifest")
        with self.assertRaisesRegex(RuntimeError, "fresh SDK directory"):
            builder.verify_upstream_runtime(self.sdk)
        marker.unlink()
        path = self.sdk / next(iter(builder.STOCK_HASHES))
        path.chmod(0o644)
        path.write_bytes(b"unmarked-patched-runtime")
        with self.assertRaisesRegex(RuntimeError, "neither the pinned stock"):
            builder.verify_upstream_runtime(self.sdk)

    def test_archive_traversal_links_devices_and_duplicate_paths_are_rejected(self):
        root = builder.SOURCE_ROOT
        for name, kind in [
            (f"{root}/../outside", tarfile.REGTYPE),
            ("/absolute", tarfile.REGTYPE),
            ("wrong-root/file", tarfile.REGTYPE),
            (f"{root}/link", tarfile.SYMTYPE),
            (f"{root}/hardlink", tarfile.LNKTYPE),
            (f"{root}/pipe", tarfile.FIFOTYPE),
        ]:
            with self.subTest(name=name, kind=kind):
                archive = self.archive([(name, kind, b"x")])
                with self.assertRaisesRegex(RuntimeError, "unsafe or duplicate"):
                    builder._extract_source(archive, self.base / "extract")
        archive = self.archive([(f"{root}/file", tarfile.REGTYPE, b"a"),
                                (f"{root}/file", tarfile.REGTYPE, b"b")])
        with self.assertRaisesRegex(RuntimeError, "unsafe or duplicate"):
            builder._extract_source(archive, self.base / "extract")
        self.assertFalse((self.base / "extract").exists())
        self.assertFalse((self.base / "outside").exists())

    def test_extraction_preserves_execution_but_strips_special_permissions(self):
        root = builder.SOURCE_ROOT
        archive = self.archive([(f"{root}/CMakeLists.txt", tarfile.REGTYPE, b"project(test)"),
                                (f"{root}/tools/build", tarfile.REGTYPE, b"#!/bin/sh\n")])
        source = builder._extract_source(archive, self.base / "extract")
        self.assertEqual((source / "tools/build").stat().st_mode & 0o7777, 0o755)
        self.assertEqual((source / "tools/build").read_bytes(), b"#!/bin/sh\n")

    def test_install_replaces_readonly_files_and_cache_checks_every_artifact(self):
        include = self.sdk / "include" / "api.h"
        include.parent.mkdir()
        include.write_text("unchanged official API")
        built = self.built()
        manifest = builder._install(self.sdk, built, self.inputs)
        self.assertEqual(builder._verify_sdk(self.sdk, self.inputs), manifest)
        self.assertEqual(set(manifest["artifacts"]), set(builder.ARTIFACTS))
        for name, source in built.items():
            self.assertEqual((self.sdk / name).read_bytes(), source.read_bytes())
        self.assertEqual(include.read_text(), "unchanged official API")
        symbols = self.sdk / "lib64/debug/libdynamorio.so.debug"
        symbols.write_bytes(b"tampered-symbols")
        with self.assertRaisesRegex(RuntimeError, "hash mismatch"):
            builder._verify_sdk(self.sdk, self.inputs)

    def test_matching_cache_skips_build_but_tampered_binary_fails(self):
        current_inputs = builder._inputs(builder.PATCH.read_bytes())
        builder._install(self.sdk, self.built(), current_inputs)
        with mock.patch.object(builder, "_get_archive") as download:
            self.assertEqual(builder.build_runtime(self.sdk, self.base / "work")["inputs"], current_inputs)
            download.assert_not_called()
            binary = self.sdk / "lib64/release/libdynamorio.so"
            binary.write_bytes(b"tampered-runtime")
            with self.assertRaisesRegex(RuntimeError, "hash mismatch"):
                builder.build_runtime(self.sdk, self.base / "work")
            download.assert_not_called()

    def test_changed_patch_requires_rebuild_after_prior_cache_is_verified(self):
        builder._install(self.sdk, self.built(), self.inputs)
        self.assertIsNone(builder._verify_sdk(self.sdk, builder._inputs(b"next-patch")))
        symbols = self.sdk / "lib64/release/libdynamorio.so.debug"
        symbols.write_bytes(b"changed")
        with self.assertRaisesRegex(RuntimeError, "hash mismatch"):
            builder._verify_sdk(self.sdk, builder._inputs(b"next-patch"))

    def test_missing_new_symbols_remove_stale_stock_symbols(self):
        manifest = builder._install(self.sdk, self.built(symbols=False), self.inputs)
        self.assertEqual(builder._verify_sdk(self.sdk, self.inputs), manifest)
        for name in builder.ARTIFACTS:
            if name.endswith(".debug"):
                self.assertIsNone(manifest["artifacts"][name])
                self.assertFalse((self.sdk / name).exists())
        (self.sdk / "lib64/release/libdynamorio.so.debug").write_bytes(b"foreign-symbols")
        with self.assertRaisesRegex(RuntimeError, "unrecorded"):
            builder._verify_sdk(self.sdk, self.inputs)

    def test_partial_install_failure_rolls_back_readonly_originals(self):
        real_replace = builder.os.replace
        calls = 0

        def replace_once_failing(source, target):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("simulated installation failure")
            return real_replace(source, target)

        with mock.patch.object(builder.os, "replace", side_effect=replace_once_failing):
            with self.assertRaisesRegex(OSError, "simulated installation"):
                builder._install(self.sdk, self.built(), self.inputs)
        for name, data in self.stock.items():
            self.assertEqual((self.sdk / name).read_bytes(), data)
            self.assertEqual((self.sdk / name).stat().st_mode & 0o777, 0o444)
        self.assertFalse((self.sdk / builder.MANIFEST).exists())

    def test_failed_second_profile_build_never_changes_the_sdk(self):
        archive = self.base / "placeholder.tar.gz"
        archive.write_bytes(b"test source")
        source = self.base / "source"
        source.mkdir()
        with mock.patch.object(builder, "_get_archive", return_value=archive), \
                mock.patch.object(builder, "_check_archive"), \
                mock.patch.object(builder, "_extract_source", return_value=source), \
                mock.patch.object(builder, "_command"), \
                mock.patch.object(builder, "_compile_profiles", side_effect=RuntimeError("debug build failed")):
            with self.assertRaisesRegex(RuntimeError, "debug build failed"):
                builder.build_runtime(self.sdk, self.base / "work")
        for name, data in self.stock.items():
            self.assertEqual((self.sdk / name).read_bytes(), data)
        self.assertFalse((self.sdk / builder.MANIFEST).exists())

    def test_malformed_manifest_and_profile_symlink_are_rejected(self):
        (self.sdk / builder.MANIFEST).write_text('{"schema":1,"schema":1}')
        with self.assertRaisesRegex(RuntimeError, "invalid runtime manifest"):
            builder._verify_sdk(self.sdk, self.inputs)
        (self.sdk / builder.MANIFEST).unlink()
        profile = self.sdk / "lib64/release"
        saved = self.sdk / "saved-release"
        profile.rename(saved)
        profile.symlink_to(saved, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, "directory is missing or is a symlink"):
            builder._verify_sdk(self.sdk, self.inputs)


if __name__ == "__main__":
    unittest.main()
