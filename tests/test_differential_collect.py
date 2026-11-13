"""Exercise real bounded collector subprocesses and untrusted replay bundles."""
import contextlib
import io
import json
import os
from pathlib import Path
import signal
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

import differential_collect as differential


class DifferentialReplayTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='durante-replay-test-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        case = json.loads((differential.ROOT / 'tests/protocol_vectors.json').read_text())['cases'][0]
        self.root_pid = case['root_pid']
        self.files = {name: differential.encode(rows) for name, rows in case['files'].items()}
        directory = self.root / 'reference-input'
        directory.mkdir()
        for name, data in self.files.items():
            (directory / name).write_bytes(data)
        self.good = differential.reference.collect(directory, self.root_pid)
        self.assertTrue(self.good['valid'])
        self.bad = dict(self.good)
        self.bad['instructions'] += 1
        self.original = self.collector('original', 'import json\nprint(' + repr(json.dumps(self.bad)) + ')\n')
        self.current = self.collector('current', 'print(' + repr(json.dumps(self.good)) + ')\n')

    def collector(self, name, source):
        path = self.root / name
        path.write_text('#!' + sys.executable + '\n' + source)
        path.chmod(0o700)
        return path

    def invoke(self, binary, output, *arguments):
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            code = differential.main(['--collector-bin', str(binary), '--failure-dir', str(output),
                                      *arguments])
        return code, json.loads(stream.getvalue())

    def bundle(self, name='failure', binary=None):
        path = self.root / name
        code, result = self.invoke(binary or self.original, path, '--cases', '1', '--seed', '7')
        self.assertEqual(code, 1, result)
        self.assertFalse(result['passed'])
        return path

    def edit_manifest(self, path, edit):
        file = path / 'manifest.json'
        data = json.loads(file.read_text())
        edit(data)
        file.write_text(json.dumps(data))

    def test_failure_bundle_preserves_inputs_both_outputs_and_identities(self):
        path = self.bundle()
        manifest, files = differential.load_bundle(path)
        self.assertEqual(files, self.files)
        self.assertEqual(manifest['root_pid'], self.root_pid)
        self.assertEqual(manifest['context']['seed'], 7)
        self.assertEqual(manifest['context']['case_index'], 0)
        self.assertEqual(manifest['context']['operation'], 'unmodified-vector')
        self.assertEqual(json.loads((path / 'native.stdout').read_bytes()), self.bad)
        self.assertEqual(json.loads((path / 'reference.stdout').read_bytes()), self.good)
        self.assertEqual((path / 'native.stderr').read_bytes(), b'')
        self.assertEqual((path / 'reference.stderr').read_bytes(), b'')
        self.assertEqual(manifest['tools_before'], manifest['tools_after'])
        self.assertEqual(manifest['tools_before']['native']['sha256'],
                         differential.sha256(self.original.read_bytes()))
        for name in ('native', 'reference'):
            self.assertTrue(manifest['observations'][name]['reaped'])
            self.assertEqual(manifest['observations'][name]['exit_code'], 0)
        for entry in [path, *(path.rglob('*'))]:
            self.assertEqual(stat.S_IMODE(entry.stat().st_mode), 0o700 if entry.is_dir() else 0o600)

    def test_raw_invalid_utf8_nul_and_unterminated_bytes_are_preserved(self):
        files = dict(self.files)
        name = next(iter(files))
        files[name] = b' \r\n\xff\x00{"pid":1}'
        path = self.root / 'raw-failure'
        result = differential.run_and_preserve(
            self.current, files, self.root_pid, {'seed': 7, 'case_index': 0}, path,
            differential.tool_identities(self.current))
        self.assertFalse(result['passed'])
        _, saved = differential.load_bundle(path)
        self.assertEqual(saved, files)
        self.assertEqual((path / 'records' / name).read_bytes(), files[name])

    def test_replay_uses_current_collectors_without_saved_executables_or_mutator(self):
        path = self.bundle()
        self.original.unlink()
        # A saved path is data, never an executable selection or shell command.
        self.edit_manifest(path, lambda data: data['tools_before']['native'].update(
            path='/definitely/missing; touch DO_NOT_EXECUTE'))
        with patch.object(differential, 'mutate', side_effect=AssertionError('mutator called')):
            code, result = self.invoke(self.current, self.root / 'replay-failure', '--replay', str(path))
        self.assertEqual(code, 0, result)
        self.assertTrue(result['passed'])
        self.assertTrue(result['replay'])
        self.assertEqual(result['current_tools']['native']['path'], str(self.current))
        self.assertFalse((self.root / 'replay-failure').exists())

    def test_still_failing_replay_preserves_a_new_bundle(self):
        path = self.bundle()
        original = (path / 'manifest.json').read_bytes()
        second = self.root / 'replayed-failure'
        code, result = self.invoke(self.original, second, '--replay', str(path))
        self.assertEqual(code, 1, result)
        self.assertEqual((path / 'manifest.json').read_bytes(), original)
        replay, files = differential.load_bundle(second)
        self.assertEqual(files, self.files)
        self.assertEqual(replay['context']['original_context']['seed'], 7)

    def test_crash_keeps_raw_diagnostics_and_still_runs_reference(self):
        crashing = self.collector('crash', "import os,signal\nos.write(1,b'partial\\xff')\n"
                                  "os.write(2,b'crash evidence\\n')\nos.kill(os.getpid(),signal.SIGKILL)\n")
        path = self.bundle(binary=crashing)
        manifest, _ = differential.load_bundle(path)
        self.assertEqual(manifest['observations']['native']['exit_code'], -signal.SIGKILL)
        self.assertEqual((path / 'native.stdout').read_bytes(), b'partial\xff')
        self.assertEqual((path / 'native.stderr').read_bytes(), b'crash evidence\n')
        self.assertEqual(json.loads((path / 'reference.stdout').read_bytes()), self.good)

    def test_launch_error_is_saved(self):
        self.original.chmod(0o600)
        path = self.bundle()
        manifest, _ = differential.load_bundle(path)
        self.assertIsNone(manifest['observations']['native']['exit_code'])
        self.assertIsNotNone(manifest['observations']['native']['error'])
        self.assertEqual(json.loads((path / 'reference.stdout').read_bytes()), self.good)

    def test_invalid_results_and_diagnostics_are_failures(self):
        invalid = dict(self.good, valid=False, complete=False, rep_telemetry_status='unavailable')
        cases = {
            'partial-totals': 'import sys\nprint(' + repr(json.dumps(invalid)) + ')\nsys.exit(1)\n',
            'malformed-json': "print('{invalid')\n",
            'diagnostic-stderr': "import sys\nsys.stderr.write('diagnostic')\nprint(" + repr(json.dumps(self.good)) + ')\n',
            'wrong-status': 'import sys\nprint(' + repr(json.dumps(self.good)) + ')\nsys.exit(1)\n',
        }
        for name, source in cases.items():
            with self.subTest(name=name):
                path = self.bundle(name, self.collector(name + '-collector', source))
                manifest, _ = differential.load_bundle(path)
                self.assertFalse(manifest['failure']['passed'])
                self.assertEqual(json.loads((path / 'reference.stdout').read_bytes()), self.good)
                if name == 'partial-totals':
                    self.assertIn('partial totals', manifest['failure']['reason'])

    def test_deadline_and_output_bound_terminate_and_reap(self):
        sleeping = self.collector('sleep', 'import time\ntime.sleep(60)\n')
        row = differential.run_collector([str(sleeping)], timeout=0.1)
        self.assertTrue(row['timed_out'])
        self.assertTrue(row['reaped'])
        self.assertEqual(row['exit_code'], -signal.SIGKILL)
        flooding = self.collector('flood', "import os\nwhile True: os.write(1,b'x'*65536)\n")
        row = differential.run_collector([str(flooding)])
        self.assertTrue(row['output_limit'])
        self.assertTrue(row['reaped'])
        self.assertEqual(len(row['stdout']), differential.MAX_CAPTURE)
        self.assertEqual(row['stdout'], b'x' * differential.MAX_CAPTURE)

    def test_corrupt_bundle_is_rejected_before_collectors_start(self):
        path = self.bundle()
        self.edit_manifest(path, lambda data: data['records'][0].update(name='../escape'))
        with patch.object(differential, 'inspect_case', side_effect=AssertionError('collector ran')):
            code, result = self.invoke(self.current, self.root / 'unused', '--replay', str(path))
        self.assertEqual(code, 2)
        self.assertIn('unsafe', result['reason'])

    def test_unsafe_names_duplicates_and_manifest_numbers_are_rejected(self):
        path = self.bundle()
        original = (path / 'manifest.json').read_bytes()
        edits = {
            'traversal': lambda data: data['records'][0].update(name='../escape'),
            'absolute': lambda data: data['records'][0].update(name='/tmp/escape'),
            'duplicate record': lambda data: data['records'].append(data['records'][0]),
            'schema bool': lambda data: data.update(schema=True),
            'root bool': lambda data: data.update(root_pid=True),
            'oversize total': lambda data: data['records'][0].update(bytes=differential.MAX_RECORD + 1),
            'bad digest': lambda data: data['records'][0].update(sha256='0' * 64),
            'missing output': lambda data: data['outputs'].pop('native.stderr'),
        }
        for name, edit in edits.items():
            with self.subTest(name=name):
                (path / 'manifest.json').write_bytes(original)
                self.edit_manifest(path, edit)
                with self.assertRaises(ValueError):
                    differential.load_bundle(path)
        for invalid in (b'{"schema":1,"schema":1}',
                        original.replace(b'"schema": 1', b'"schema": NaN'),
                        original.replace(b'"schema": 1', b'"schema": 1e999')):
            (path / 'manifest.json').write_bytes(invalid)
            with self.assertRaises(ValueError):
                differential.load_bundle(path)

    def test_unsafe_bundle_leaf_types_and_growth_are_rejected(self):
        for mode in ('symlink', 'hardlink', 'fifo', 'oversize', 'extra', 'root-symlink', 'records-symlink'):
            with self.subTest(mode=mode):
                path = self.bundle(mode)
                leaf = path / 'records' / next(iter(self.files))
                if mode in ('symlink', 'hardlink', 'fifo'):
                    leaf.unlink()
                    victim = self.root / ('victim-' + mode)
                    victim.write_bytes(next(iter(self.files.values())))
                    if mode == 'symlink':
                        leaf.symlink_to(victim)
                    elif mode == 'hardlink':
                        os.link(victim, leaf)
                    else:
                        os.mkfifo(leaf)
                elif mode == 'oversize':
                    with leaf.open('wb') as stream:
                        stream.truncate(differential.MAX_RECORD + 1)
                elif mode == 'extra':
                    (path / 'undeclared').write_text('unexpected')
                elif mode == 'root-symlink':
                    alias = self.root / 'bundle-link'
                    alias.symlink_to(path, target_is_directory=True)
                    path = alias
                else:
                    records = path / 'records'
                    moved = self.root / 'moved-records'
                    records.rename(moved)
                    records.symlink_to(moved, target_is_directory=True)
                with self.assertRaises((OSError, ValueError)):
                    differential.load_bundle(path)

    def test_manifest_and_capture_bounds_and_capture_digest_are_checked(self):
        for mode in ('manifest-size', 'output-size', 'output-digest'):
            with self.subTest(mode=mode):
                path = self.bundle(mode)
                if mode == 'manifest-size':
                    leaf, size = path / 'manifest.json', differential.MAX_MANIFEST + 1
                elif mode == 'output-size':
                    leaf, size = path / 'native.stderr', differential.MAX_CAPTURE + 1
                else:
                    (path / 'native.stdout').write_bytes(b'changed capture')
                    with self.assertRaises(ValueError):
                        differential.load_bundle(path)
                    continue
                with leaf.open('wb') as stream:
                    stream.truncate(size)
                with self.assertRaises(ValueError):
                    differential.load_bundle(path)

    def test_existing_failure_destination_never_overwrites(self):
        path = self.bundle()
        original = (path / 'manifest.json').read_bytes()
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            self.invoke(self.original, path, '--cases', '1')
        self.assertEqual(caught.exception.code, 2)
        self.assertEqual((path / 'manifest.json').read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
