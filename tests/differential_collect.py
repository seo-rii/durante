#!/usr/bin/env python3
"""Bounded differential mutation testing of both production collectors.

Protocol vectors seed valid and invalid trees. Mutations preserve useful JSON
structure or attack raw framing/UTF-8. The native CLI and Python filesystem
collector must agree on validity and every accepted aggregate. A failing case is
reproducible with the printed seed and --cases up through its case index.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import random
import subprocess
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
    if result.get('valid') is not True:
        if result.get('complete') is not False:
            raise AssertionError('invalid result was marked complete')
        if any(key in result for key in ('instructions', 'rep_bytes', 'rep_entries',
               'unsupported_rep_entries', 'signals_observed', 'processes', 'segments')):
            raise AssertionError('invalid result exposed partial totals')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--collector-bin', type=Path, required=True)
    parser.add_argument('--cases', type=int, default=512)
    parser.add_argument('--seed', type=int, default=20261111)
    args = parser.parse_args()
    if not 1 <= args.cases <= 10000:
        parser.error('cases must be between 1 and 10000')
    binary = args.collector_bin.resolve(strict=True)
    vectors = json.loads((ROOT / 'tests/protocol_vectors.json').read_text())['cases']
    rng = random.Random(args.seed)
    started = time.monotonic()
    accepted = rejected = 0
    with tempfile.TemporaryDirectory(prefix='durante-differential-') as temporary:
        directory = Path(temporary)
        for index in range(args.cases):
            if time.monotonic() - started > 120:
                raise RuntimeError('differential campaign exceeded 120 seconds')
            # Numeric/grammar mutations start from a known complete tree, so
            # an unrelated pre-existing invalid graph cannot mask disagreement.
            case = vectors[0] if index % 12 in (1, 2, 3, 4, 5) else vectors[index % len(vectors)]
            files, operation = mutate(case, index, rng)
            for path in directory.iterdir():
                path.unlink()
            for name, data in files.items():
                (directory / name).write_bytes(data)
            try:
                native = subprocess.run([str(binary), 'collect', '--dir', str(directory),
                                         '--root-pid', str(case['root_pid'])],
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=3)
                if native.returncode not in (0, 1) or len(native.stdout) > 1 << 20:
                    raise AssertionError('native collector exited unexpectedly or exceeded output bound')
                rust = json.loads(native.stdout)
                python = reference.collect(directory, case['root_pid'])
                no_partial_totals(rust); no_partial_totals(python)
                if rust['valid'] != python['valid']:
                    raise AssertionError('collector validity differs')
                if rust['valid']:
                    if rust != python or native.returncode != 0:
                        raise AssertionError('accepted aggregates differ')
                    accepted += 1
                else:
                    if native.returncode != 1:
                        raise AssertionError('invalid aggregate used a successful exit')
                    rejected += 1
            except (AssertionError, OSError, ValueError, subprocess.SubprocessError) as error:
                print(json.dumps({'passed': False, 'seed': args.seed, 'case_index': index,
                                  'baseline': case['name'], 'operation': operation,
                                  'reason': str(error)}))
                return 1
    print(json.dumps({'passed': True, 'seed': args.seed, 'cases': args.cases,
                      'accepted': accepted, 'rejected': rejected,
                      'elapsed_seconds': round(time.monotonic() - started, 3)}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
