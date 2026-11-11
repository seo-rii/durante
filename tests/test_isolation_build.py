import hashlib
import importlib.util
import io
import json
from pathlib import Path
import stat
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    'isolation_build', Path(__file__).resolve().parents[1] / 'toolkit/build_isolation.py')
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


class IsolationBuildTests(unittest.TestCase):
    def archive(self, directory, entries):
        target = directory / 'source.tar.xz'
        with tarfile.open(target, 'w:xz') as archive:
            for name, content, kind in entries:
                member = tarfile.TarInfo(name)
                member.mode = 0o6777
                if kind == 'link':
                    member.type = tarfile.SYMTYPE
                    member.linkname = content
                    archive.addfile(member)
                else:
                    data = content.encode()
                    member.size = len(data)
                    archive.addfile(member, io.BytesIO(data))
        return target

    def installation(self, root):
        output = root / 'bwrap'
        output.write_bytes(b'fixture executable')
        output.chmod(0o755)
        manifest = {'schema': 1, 'inputs': builder.inputs(),
                    'binary_sha256': hashlib.sha256(output.read_bytes()).hexdigest()}
        builder.manifest_path(output).write_text(json.dumps(manifest))
        return output

    def test_corrupt_source_is_rejected_before_extraction(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'archive'
            source.write_bytes(b'not the pinned archive')
            with self.assertRaises(ValueError):
                builder.check_archive(source)

    def test_traversal_escaping_links_and_duplicate_members_are_rejected(self):
        prefix = builder.SOURCE_ROOT
        for entries in [
            [(prefix + '/../escape', 'x', 'file')],
            [(prefix + '/link', '../../escape', 'link')],
            [(prefix + '/same', 'x', 'file'), (prefix + '/same', 'y', 'file')],
        ]:
            with self.subTest(entries=entries), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source = self.archive(root, entries)
                destination = root / 'extract'
                destination.mkdir()
                with self.assertRaises(ValueError):
                    builder.extract(source, destination)
                self.assertEqual(list(destination.iterdir()), [])

    def test_extraction_strips_special_and_write_permissions_and_skips_aliases(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prefix = builder.SOURCE_ROOT
            source = self.archive(root, [(prefix + '/COPYING', 'license', 'file'),
                                         (prefix + '/LICENSE', 'COPYING', 'link')])
            destination = root / 'extract'
            destination.mkdir()
            result = builder.extract(source, destination)
            self.assertEqual(stat.S_IMODE((result / 'COPYING').stat().st_mode), 0o755)
            self.assertFalse((result / 'LICENSE').exists())

    def test_cache_requires_actual_immutable_binary_and_exact_recipe(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = self.installation(root)
            self.assertEqual(builder.cached(output)['inputs'], builder.inputs())
            output.write_bytes(b'tampered')
            with self.assertRaises(ValueError):
                builder.cached(output)
            output = self.installation(root)
            output.chmod(0o4755)
            with self.assertRaises(ValueError):
                builder.cached(output)
            output.chmod(0o755)
            marker = builder.manifest_path(output)
            marker.write_text('{"schema":1,"schema":1}')
            with self.assertRaises(ValueError):
                builder.cached(output)
            for change in ['list', 'schema', 'recipe']:
                output = self.installation(root)
                document = json.loads(builder.manifest_path(output).read_text())
                if change == 'list':
                    document = []
                elif change == 'schema':
                    document['schema'] = True
                else:
                    document['inputs']['recipe'] = True
                builder.manifest_path(output).write_text(json.dumps(document))
                with self.assertRaises(ValueError):
                    builder.cached(output)

    def test_patch_tampering_and_symlinked_installation_lock_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_patch = root / 'patch'
            fake_patch.write_bytes(b'changed patch')
            with patch.object(builder, 'PATCH', fake_patch), self.assertRaises(ValueError):
                builder.build(root / 'bwrap', root / 'cache')
            victim = root / 'victim'
            victim.write_text('unchanged')
            output = root / 'bwrap'
            output.with_name(output.name + '.durante-isolation.lock').symlink_to(victim)
            with self.assertRaises(OSError):
                with builder.installation_lock(output):
                    self.fail('symlink lock was accepted')
            self.assertEqual(victim.read_text(), 'unchanged')

    def test_failed_compilation_does_not_publish_an_executable_or_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.archive(root, [(builder.SOURCE_ROOT + '/bubblewrap.c', 'source', 'file')])
            output = root / 'bwrap'
            with patch.object(builder, 'check_archive'), patch.object(builder.subprocess, 'run',
                    side_effect=subprocess.CalledProcessError(1, 'patch')):
                with self.assertRaises(subprocess.CalledProcessError):
                    builder.build(output, root / 'cache', source)
            self.assertFalse(output.exists())
            self.assertFalse(builder.manifest_path(output).exists())


if __name__ == '__main__':
    unittest.main()
