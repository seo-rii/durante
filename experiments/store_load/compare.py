#!/usr/bin/env python3
"""Compare two offsets on one static store/load control, validating every replay."""
import argparse
import json
import os
from pathlib import Path
import re
import statistics
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from collect_branch_model import branch_counts
from collect_load_model import checked_run, quoted, run_with_meter
from joint_latency_screen import FULL_COUNTERS, recover_counters
from l1d_probe.compare import digest, measure as measure_l1d
from reference_features import require


def replay(tools, program, stdin, output, cpu):
    expected = b'ok\n'
    prefix = ['env', f'DYNAMORIO_CONFIGDIR={output}', 'setarch', 'x86_64', '-R']
    output.mkdir(mode=0o700)
    _, before = checked_run([*prefix, program], stdin, expected, 120)
    dual = output / 'features'
    report = dual / 'features.json'
    pid, _, meter = run_with_meter(tools, program, stdin, expected, dual,
                                   '-out ' + quoted(report) + ' -bpred', 120, prefix=prefix)
    probe = json.loads(report.read_bytes())
    counters = {k: meter[k] for k in ('instructions', 'rep_bytes')}
    counters.update(branch_counts(probe, meter))
    raw = (dual / 'records' / f'process-{pid}.jsonl').read_bytes()
    counters.update(recover_counters(counters, probe, pid, raw))
    require(set(counters) == set(FULL_COUNTERS), 'incomplete counter set')

    case = (stdin.name, program, stdin, digest(output.parent / 'oracle'))
    cache = measure_l1d(tools['drrun'], case, output, tools['l1d'], cpu, 120, True)
    require(cache['work']['instructions'] == counters['instructions'] and
            cache['work']['rep_bytes'] == counters['rep_bytes'] and cache['cache']['skipped'] == 0,
            'live and feature replays disagree or the L1D probe skipped an operand')

    cmd = [*prefix, tools['drrun'], '-quiet', '-disable_traces', '-c', tools['store_load'], '--', program]
    proc = subprocess.run(cmd, input=stdin.read_bytes(), capture_output=True, check=True, timeout=120)
    require(proc.stdout == expected, 'store/load replay produced the wrong output')
    match = re.fullmatch(rb'STORE_LOAD_V1 covered=(\d+) partial=(\d+) disjoint=(\d+)\s*', proc.stderr)
    require(match, 'expected one store/load report')
    pair = dict(zip(('covered', 'partial', 'disjoint'), map(int, match.groups())))
    _, after = checked_run([*prefix, program], stdin, expected, 120)
    return {'native_before_ns': before, 'native_after_ns': after,
            'counters': counters, 'cache': cache['cache'], 'store_load': pair}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('program', 'drrun', 'meter', 'time-features', 'l1d', 'store-load', 'output'):
        parser.add_argument('--' + name, required=True, type=Path)
    parser.add_argument('--cpu', type=int, required=True)
    args = parser.parse_args()
    os.sched_setaffinity(0, {args.cpu})
    tools = {name: getattr(args, name).resolve() for name in ('drrun', 'meter', 'l1d', 'store_load')}
    tools['probe'] = args.time_features.resolve()
    program = args.program.resolve()
    files = {'program': program, 'core': tools['drrun'].parents[1] / 'lib64/release/libdynamorio.so', **tools}
    hashes = {name: digest(path) for name, path in files.items()}
    output = args.output.resolve()
    output.mkdir(mode=0o700)
    (output / 'oracle').write_bytes(b'ok\n')
    samples = {'covered': [], 'partial': []}
    for mode, byte in [('covered', b'0'), ('partial', b'1')]:
        (output / mode).write_bytes(byte)
    for n in range(3):
        for mode in (samples if n % 2 == 0 else reversed(samples)):
            row = replay(tools, program, output / mode, output / f'{mode}-{n+1}', args.cpu)
            require(row['store_load'] == {k: (96_000_000 if k == mode else 0)
                                          for k in ('covered', 'partial', 'disjoint')},
                    f'{mode}: incorrect byte-range classification')
            samples[mode].append(row)
    first = samples['covered'][0]
    require(all(row[k] == first[k] for rows in samples.values() for row in rows
                for k in ('counters', 'cache')), 'the input pair had different counter vectors')
    require(all(digest(path) == hashes[name] for name, path in files.items()), 'an input binary changed')
    native = {mode: [row[k] for row in rows for k in ('native_before_ns', 'native_after_ns')]
              for mode, rows in samples.items()}
    # If the two ranges are disjoint, use their closest endpoints in either order.
    lhs, rhs = sorted(native.values(), key=max)
    fast_max, slow_min = max(lhs), min(rhs)
    summary = {'sha256': hashes, 'cpu': args.cpu, 'samples': samples,
               'native_median_ns': {k: statistics.median(v) for k, v in native.items()},
               'range_error_lower_bound': max(0, slow_min - fast_max) / (slow_min + fast_max)}
    (output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')


if __name__ == '__main__':
    main()
