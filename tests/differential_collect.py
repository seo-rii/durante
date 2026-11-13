#!/usr/bin/env python3
"""Bounded differential mutation testing of both production collectors.

Protocol vectors seed valid and invalid trees. Mutations preserve useful JSON
structure or attack raw framing/UTF-8. The native CLI and Python filesystem
collector must agree on validity and every accepted aggregate. A failing case is
preserved as a bounded bundle of raw records, collector outputs and identities.
Replay uses current collectors; paths and commands in saved metadata are never run.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import random
import re
import selectors
import signal
import stat
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('durante_reference', ROOT / 'collect.py')
reference = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reference)

NUMBERS = (
    '0', '-0', '0.0', '-0.0', '0e0', '1', '-1', '1.0', 'true', 'false', 'null',
    '9223372036854775807', '9223372036854775808', '-9223372036854775808',
    '-9223372036854775809', '18446744073709551615', '18446744073709551616',
    '1e309', '-1e309', '1e-999', '0e999999999999999999999',
    '9' * 100, '-' + '9' * 100, '9' * 500, '-' + '9' * 500,
    str(int(float.fromhex('0x1.fffffffffffffp+1023'))),
    '1.7976931348623157e308', '1.7976931348623158e308',
)
FIELDS = ('extra', 'result', 'parent_pid', 'instructions', 'seq', 'schema', 'valid',
          'pid', 'supported', 'rep_bytes_enabled', 'parent_segment')


def encode(rows):
    return b''.join(json.dumps(row, separators=(',', ':')).encode() + b'\n' for row in rows)


def mutate(case, index, rng):
    files = {name: encode(rows) for name, rows in case['files'].items()}
    name = rng.choice(sorted(files))
    original = case['files'][name]
    operation = index % 12
    if operation == 0:
        return files, 'unmodified-vector'
    row_index = rng.randrange(len(original))
    rows = [dict(row) for row in original]
    if operation in (1, 2, 3):
        field = rng.choice(FIELDS) if operation != 1 else 'extra'
        token = rng.choice(NUMBERS)
        rows[row_index].pop(field, None)
        lines = [json.dumps(row, separators=(',', ':')) for row in rows]
        lines[row_index] = lines[row_index][:-1] + ',' + json.dumps(field) + ':' + token + '}'
        files[name] = ('\n'.join(lines) + '\n').encode()
        return files, 'numeric-or-scalar-' + field
    if operation == 4:
        # Different lexical spellings must still count as duplicate keys.
        data = files[name]
        files[name] = data.replace(b'"pid":', b'"\\u0070id":1,"pid":', 1)
    elif operation == 5:
        lines = [json.dumps(row, separators=(',', ':')) for row in rows]
        token = rng.choice(['[1]', '{"nested":1}', '[' * 1100 + '0' + ']' * 1100,
                            '"\\ud800"', '"escaped \\\" { [ } text"'])
        lines[row_index] = lines[row_index][:-1] + ',"extra":' + token + '}'
        files[name] = ('\n'.join(lines) + '\n').encode()
    elif operation == 6:
        files[name] = files[name].replace(b'\n', b'\r\n')
    elif operation == 7:
        files[name] = files[name][:-1]
    elif operation == 8:
        position = rng.randrange(len(files[name]) + 1)
        files[name] = files[name][:position] + b'\xff' + files[name][position:]
    elif operation == 9:
        files[name] = b'\n' + files[name]
    elif operation == 10:
        position = rng.randrange(len(files[name]))
        data = bytearray(files[name]); data[position] ^= rng.randrange(1, 256)
        files[name] = bytes(data)
    else:
        # Unknown scalar strings stay legal up to the actual line-size bound.
        rows[row_index]['extra'] = 'a' * rng.choice([0, 100, 2600, 3072])
        files[name] = encode(rows)
    return files, 'mutation-' + str(operation)


def no_partial_totals(result):
    if result.get('authority') != 'diagnostic_only':
        raise AssertionError('authority was promoted')
    if result.get('integrity_verified') is not False or result.get('measurement_contract') != reference.measurement_contract():
        raise AssertionError('measurement contract was missing or promoted')
    if result.get('valid') is not True:
        if result.get('rep_telemetry_status') != 'unavailable':
            raise AssertionError('invalid result exposed REP coverage')
        if result.get('complete') is not False:
            raise AssertionError('invalid result was marked complete')
        if any(key in result for key in ('instructions', 'rep_bytes', 'rep_entries',
               'unsupported_rep_entries', 'signals_observed', 'processes', 'segments')):
            raise AssertionError('invalid result exposed partial totals')


# Limits cover the current mutation corpus, including one over-limit record.
# Large output is retained only as a marked prefix; raw input is never truncated.
MAX_FILES = 257
MAX_RECORD = (1 << 20) + 1
MAX_RECORD_TOTAL = 16 << 20
MAX_CAPTURE = 1 << 20
MAX_MANIFEST = 256 << 10
OUTPUTS = ('native.stdout', 'native.stderr', 'reference.stdout', 'reference.stderr')
NAME = re.compile(r'process-[0-9]{1,10}\.jsonl\Z', re.ASCII)
SHA256 = re.compile(r'[0-9a-f]{64}\Z', re.ASCII)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def tool_identities(binary):
    tools = {}
    for name, path in (('native', binary), ('reference', ROOT / 'collect.py'),
                       ('python', Path(sys.executable).resolve()),
                       ('harness', Path(__file__).resolve()),
                       ('vectors', ROOT / 'tests/protocol_vectors.json')):
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        tools[name] = {'path': str(path), 'sha256': digest}
    tools['python']['version'] = sys.version
    # Metadata only. A binary hash does not attest which source built it.
    revision = subprocess.run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'],
                              capture_output=True, text=True, timeout=3, check=False)
    tools['source_revision'] = revision.stdout.strip() if revision.returncode == 0 else None
    return tools


def run_collector(command, timeout=3):
    """Bound trusted collector execution and capture, including both error pipes."""
    result = {'exit_code': None, 'timed_out': False, 'output_limit': False,
              'error': None, 'pid': None, 'reaped': False, 'stdout': b'', 'stderr': b''}
    process = None
    selector = selectors.DefaultSelector()
    captured = {'stdout': bytearray(), 'stderr': bytearray()}
    deadline = time.monotonic() + timeout
    try:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   start_new_session=True)
        result['pid'] = process.pid
        for name, stream in (('stdout', process.stdout), ('stderr', process.stderr)):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, name)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                result['timed_out'] = True
                break
            for key, _ in selector.select(min(remaining, 0.05)):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    key.fileobj.close()
                    continue
                buffer = captured[key.data]
                space = MAX_CAPTURE - len(buffer)
                buffer.extend(chunk[:space])
                if len(chunk) > space:
                    result['output_limit'] = True
                    break
            if result['output_limit']:
                break
        if not result['timed_out'] and not result['output_limit']:
            try:
                process.wait(timeout=max(0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                result['timed_out'] = True
    except (OSError, ValueError) as error:
        result['error'] = str(error)[:500]
    finally:
        # The collectors are trusted executables, not arbitrary workload runners.
        # A failed collector's original process group is stopped before waiting.
        if process is not None:
            if process.returncode is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                result['error'] = 'collector did not exit after termination'
            result['exit_code'] = process.returncode
            result['reaped'] = process.returncode is not None
            for stream in (process.stdout, process.stderr):
                if not stream.closed:
                    stream.close()
        selector.close()
        result.update({name: bytes(value) for name, value in captured.items()})
    return result


def compare(observations):
    try:
        parsed = {}
        for name in ('native', 'reference'):
            row = observations[name]
            if row['error'] or row['timed_out'] or row['output_limit'] or not row['reaped']:
                raise AssertionError(name + ' collector execution/capture failed')
            if row['exit_code'] not in (0, 1):
                raise AssertionError(name + ' collector exited unexpectedly')
            if row['stderr']:
                raise AssertionError(name + ' collector wrote diagnostics')
            report = json.loads(row['stdout'])
            if type(report) is not dict or type(report.get('valid')) is not bool:
                raise AssertionError(name + ' collector returned a non-protocol result')
            no_partial_totals(report)
            if row['exit_code'] != (0 if report['valid'] else 1):
                raise AssertionError(name + ' collector exit disagrees with validity')
            parsed[name] = report
        rust, python = parsed['native'], parsed['reference']
        if rust['valid'] != python['valid']:
            raise AssertionError('collector validity differs')
        if rust['valid'] and rust != python:
            raise AssertionError('accepted aggregates differ')
        return {'passed': True, 'valid': rust['valid'], 'reason': None}
    except (AssertionError, ValueError, UnicodeError, KeyError, TypeError, RecursionError) as error:
        return {'passed': False, 'valid': None, 'reason': str(error)[:500]}


def valid_inputs(files, root_pid):
    if type(root_pid) is not int or not 0 <= root_pid <= (1 << 32) - 1:
        raise ValueError('root PID must be a uint32')
    if not 1 <= len(files) <= MAX_FILES:
        raise ValueError('record file count exceeds bundle bounds')
    if any(type(name) is not str or not NAME.fullmatch(name) for name in files):
        raise ValueError('unsafe or unsupported record filename')
    if any(type(data) is not bytes or len(data) > MAX_RECORD for data in files.values()):
        raise ValueError('record exceeds bundle byte bound')
    if sum(map(len, files.values())) > MAX_RECORD_TOTAL:
        raise ValueError('record total exceeds bundle byte bound')


def inspect_case(binary, files, root_pid):
    valid_inputs(files, root_pid)
    # Replay copies validated bytes to an owned directory. Collectors never open
    # paths in the supplied bundle, even if it changes after validation.
    with tempfile.TemporaryDirectory(prefix='durante-differential-') as temporary:
        directory = Path(temporary)
        for name, data in files.items():
            (directory / name).write_bytes(data)
        arguments = ['--dir', str(directory), '--root-pid', str(root_pid)]
        observations = {
            'native': run_collector([str(binary), 'collect', *arguments]),
            'reference': run_collector([sys.executable, '-I', '-B', str(ROOT / 'collect.py'),
                                        *arguments]),
        }
    return compare(observations), observations


def write_leaf(directory_fd, name, data):
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                 0o600, dir_fd=directory_fd)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)


def metadata(data):
    return {'bytes': len(data), 'sha256': sha256(data)}


def save_bundle(path, files, root_pid, context, result, observations, tools_before, tools_after):
    valid_inputs(files, root_pid)
    manifest = {'schema': 1, 'root_pid': root_pid, 'context': context,
                'failure': result, 'tools_before': tools_before, 'tools_after': tools_after,
                'records': [{'name': name, **metadata(data)} for name, data in sorted(files.items())],
                'observations': {}, 'outputs': {}}
    for name, row in observations.items():
        manifest['observations'][name] = {key: value for key, value in row.items()
                                          if key not in ('stdout', 'stderr')}
        for stream in ('stdout', 'stderr'):
            data = row[stream]
            if len(data) > MAX_CAPTURE:
                raise ValueError('collector capture exceeds bundle bound')
            manifest['outputs'][name + '.' + stream] = metadata(data)
    encoded = (json.dumps(manifest, indent=2, allow_nan=False) + '\n').encode()
    if len(encoded) > MAX_MANIFEST:
        raise ValueError('failure metadata exceeds bundle bound')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.mkdir(mode=0o700)  # A pre-existing directory, file or symlink is an error.
    directory = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.mkdir('records', mode=0o700, dir_fd=directory)
        records = os.open('records', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                          dir_fd=directory)
        try:
            for name, data in files.items():
                write_leaf(records, name, data)
        finally:
            os.close(records)
        for name, row in observations.items():
            for stream in ('stdout', 'stderr'):
                write_leaf(directory, name + '.' + stream, row[stream])
        # Publish metadata last: partial writes are not replayable bundles.
        write_leaf(directory, 'manifest.json', encoded)
    finally:
        os.close(directory)


def read_leaf(directory, name, maximum):
    fd = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=directory)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > maximum:
            raise ValueError('bundle leaf is not a bounded single-link regular file')
        data = stream.read(maximum + 1)
        after = os.fstat(stream.fileno())
    fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns', 'st_nlink')
    if len(data) != before.st_size or len(data) > maximum or any(
            getattr(before, key) != getattr(after, key) for key in fields):
        raise ValueError('bundle leaf changed while reading')
    return data


def inventory(directory, expected):
    names = set()
    with os.scandir(directory) as entries:
        for entry in entries:
            names.add(entry.name)
            if len(names) > len(expected):
                raise ValueError('unexpected bundle entries')
    if names != expected:
        raise ValueError('missing or unexpected bundle entries')


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate manifest key')
        result[key] = value
    return result


def checked_metadata(value, maximum):
    if type(value) is not dict or set(value) != {'bytes', 'sha256'}:
        raise ValueError('invalid bundle file metadata')
    if type(value['bytes']) is not int or not 0 <= value['bytes'] <= maximum:
        raise ValueError('invalid bundle file size')
    if type(value['sha256']) is not str or not SHA256.fullmatch(value['sha256']):
        raise ValueError('invalid bundle file digest')


def reject_constant(value):
    raise ValueError('non-finite JSON value in bundle')


def reject_float(value):
    raise ValueError('bundle numbers must be integers')


def load_bundle(path):
    directory = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        inventory(directory, {'manifest.json', 'records', *OUTPUTS})
        manifest = json.loads(read_leaf(directory, 'manifest.json', MAX_MANIFEST),
                              object_pairs_hook=unique_object, parse_constant=reject_constant,
                              parse_float=reject_float)
        keys = {'schema', 'root_pid', 'context', 'failure', 'tools_before', 'tools_after',
                'records', 'observations', 'outputs'}
        if type(manifest) is not dict or set(manifest) != keys or type(manifest['schema']) is not int or manifest['schema'] != 1:
            raise ValueError('unsupported bundle schema')
        if any(type(manifest[key]) is not dict for key in (
                'context', 'failure', 'tools_before', 'tools_after', 'observations')):
            raise ValueError('invalid bundle diagnostic metadata')
        if manifest['failure'].get('passed') is not False:
            raise ValueError('bundle does not record a failed comparison')
        if set(manifest['observations']) != {'native', 'reference'}:
            raise ValueError('missing collector observations')
        rows = manifest['records']
        if type(rows) is not list or not 1 <= len(rows) <= MAX_FILES:
            raise ValueError('invalid bundle record count')
        expected = {}
        for row in rows:
            if type(row) is not dict or set(row) != {'name', 'bytes', 'sha256'}:
                raise ValueError('invalid bundle record metadata')
            name = row['name']
            if type(name) is not str or not NAME.fullmatch(name) or name in expected:
                raise ValueError('unsafe or duplicate record filename')
            value = {key: row[key] for key in ('bytes', 'sha256')}
            checked_metadata(value, MAX_RECORD)
            expected[name] = value
        if sum(row['bytes'] for row in expected.values()) > MAX_RECORD_TOTAL:
            raise ValueError('record total exceeds bundle bound')
        if type(manifest['outputs']) is not dict or set(manifest['outputs']) != set(OUTPUTS):
            raise ValueError('missing collector output metadata')
        for name in OUTPUTS:
            checked_metadata(manifest['outputs'][name], MAX_CAPTURE)
            data = read_leaf(directory, name, MAX_CAPTURE)
            if metadata(data) != manifest['outputs'][name]:
                raise ValueError('collector output digest/size mismatch')
        records = os.open('records', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                          dir_fd=directory)
        try:
            inventory(records, set(expected))
            files = {}
            for name, value in expected.items():
                data = read_leaf(records, name, MAX_RECORD)
                if metadata(data) != value:
                    raise ValueError('record digest/size mismatch')
                files[name] = data
        finally:
            os.close(records)
        valid_inputs(files, manifest['root_pid'])
        return manifest, files
    finally:
        os.close(directory)


def run_and_preserve(binary, files, root_pid, context, failure_dir, before):
    result, observations = inspect_case(binary, files, root_pid)
    try:
        after = tool_identities(binary)
    except (OSError, subprocess.SubprocessError) as error:
        after = {'error': str(error)[:500]}
    if before != after:
        result = {'passed': False, 'valid': None, 'reason': 'collector or harness inputs changed'}
    if not result['passed']:
        save_bundle(failure_dir, files, root_pid, context, result, observations, before, after)
        result['bundle'] = str(failure_dir)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--collector-bin', type=Path, required=True)
    parser.add_argument('--cases', type=int, default=512)
    parser.add_argument('--seed', type=int, default=20261111)
    parser.add_argument('--failure-dir', type=Path, default=Path('.results/differential-failure'),
                        help='new private directory, created only on a failed comparison')
    parser.add_argument('--replay', type=Path, help='replay saved raw records with current collectors')
    args = parser.parse_args(argv)
    if not 1 <= args.cases <= 10000:
        parser.error('cases must be between 1 and 10000')
    if os.path.lexists(args.failure_dir):
        parser.error('--failure-dir must not already exist')
    try:
        binary = args.collector_bin.resolve(strict=True)
        before = tool_identities(binary)
        if args.replay is not None:
            original, files = load_bundle(args.replay)
            context = {'replay_of': str(args.replay), 'original_context': original['context']}
            result = run_and_preserve(binary, files, original['root_pid'], context,
                                      args.failure_dir, before)
            result.update(replay=True, original_failure=original['failure'],
                          original_tools=original['tools_before'], current_tools=before)
            print(json.dumps(result, allow_nan=False))
            return 0 if result['passed'] else 1
        vectors = json.loads((ROOT / 'tests/protocol_vectors.json').read_text())['cases']
        rng = random.Random(args.seed)
        started = time.monotonic()
        accepted = rejected = 0
        for index in range(args.cases):
            if time.monotonic() - started > 120:
                raise RuntimeError('differential campaign exceeded 120 seconds')
            case = vectors[0] if index % 12 in (1, 2, 3, 4, 5) else vectors[index % len(vectors)]
            files, operation = mutate(case, index, rng)
            context = {'seed': args.seed, 'case_index': index, 'baseline': case['name'],
                       'operation': operation}
            result = run_and_preserve(binary, files, case['root_pid'], context,
                                      args.failure_dir, before)
            if not result['passed']:
                print(json.dumps({**context, **result}, allow_nan=False))
                return 1
            accepted += result['valid']
            rejected += not result['valid']
        print(json.dumps({'passed': True, 'seed': args.seed, 'cases': args.cases,
                          'accepted': accepted, 'rejected': rejected,
                          'elapsed_seconds': round(time.monotonic() - started, 3),
                          'tools': before}, allow_nan=False))
        return 0
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, RecursionError,
            subprocess.SubprocessError) as error:
        print(json.dumps({'passed': False, 'reason': str(error)[:500]}, allow_nan=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
